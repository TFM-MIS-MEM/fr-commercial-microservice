# Instrumento de carga: MoST sobre fr-commercial

TFM-MIS-MEM/docs#120. Aplica el procedimiento MoST del capítulo 7 a un perfil de
`fr-commercial` y obtiene su **MST** (tasa máxima sostenida en un intervalo
largo), su **MIT** (la sostenida en un intervalo breve) y su **factor de
ráfaga** (MIT/MST). Con varios perfiles medidos en la misma configuración,
también los pesos σ_S(p) = MST_max / MST_p. Todo se calcula a partir de los
resúmenes crudos guardados en `results/`, sin cálculos a mano.

| Fichero | Qué es |
|---|---|
| `compose.yml` | El banco: `fr` (imagen de producción), su MongoDB, el servicio de calibración y k6. Red `internal` propia, sin puertos publicados. |
| `k6/profile.js` | Una corrida: calentamiento y ventana medida a tasa de llegada constante. |
| `criteria.py` | Las tres restricciones de calidad. Es su única definición. |
| `most.py` | Precondiciones, búsqueda, confirmación y corridas inválidas. Guarda cada corrida. |
| `analyze.py` | MST, MIT, ráfaga y σ desde `results/`, en Markdown. |
| `calibration/server.py` | Servicio sintético de capacidad conocida: 100 ms, una petición a la vez. |
| `seed.py` | Siembra y comprobación de la galería de un perfil 1:N. Corre en el contenedor `seed`. |
| `fixtures/` | La imagen de la campaña, 480 × 640 (`SOURCES.md`). |

## Qué se mide, y qué no

**La capacidad del servicio, sin la pasarela.** k6 habla con `fr` directamente
y escribe él las tres cabeceras de identidad que pondría Kong. El limitador de
la pasarela corta en 600/min por IP, y la ruta de `fr` no existe todavía
(TFM-MIS-MEM/docs#104). La validación de credenciales, el saneado y el
limitador quedan fuera de la medición.

**Una sola máquina.** k6 y `fr` comparten un MacBook Air M5 sin ventilador
(4 núcleos P + 6 E), con Docker Desktop: 10 vCPU y 7,7 GB. La validez externa
de las cifras se limita a esta máquina; lo que se puede trasladar son los
cocientes (σ y el factor de ráfaga).

### El aislamiento por cpuset es lógico, no físico

`fr` va en los vCPU 2–9; k6 y MongoDB, en 0–1. **Docker respeta el cpuset**
(medido el 2026-10-09):

| Prueba | Resultado |
|---|---|
| 6 procesos con la CPU al 100 %, fijados a 0,1 | cpu0 y cpu1 al 100 %, el resto ≤ 4 % |
| 12 procesos fijados a 2–9 | 2–9 al 100 %; 0–1 al ~20 % (el fondo de la VM) |

Pero **un vCPU no es un núcleo**. Son hilos que macOS reparte entre los núcleos
P y E, y su velocidad cae cuanto más vCPU trabajan a la vez:

| vCPU ocupados a la vez | 1 | 4 | 6 | 8 | 10 |
|---|---|---|---|---|---|
| Bucle de Python, M it/s por vCPU | 35 | 21 | 17,4 | 14,4 | 14,0 |
| Agregado | 35 | 84 | 105 | 115 | 140 |

Los 8 vCPU de `fr`, todos ocupados, rinden como ~3,3 vCPU sueltos. k6 y `fr`
no comparten vCPU, pero sí núcleos físicos. El efecto de k6 se acota midiendo
su CPU: a 7,5/s, como mucho 4,9 % de un vCPU. `fr` va en 2–9 porque 0–1 ya
cargan con el fondo de la VM.

## El procedimiento

**Una corrida.** 30 s de calentamiento al 50 % de la tasa objetivo, que se
descartan, y a continuación la ventana medida a la tasa objetivo. El
calentamiento va a la mitad para no empezar la ventana breve con cola
acumulada. Ventana larga (MST): **600 s**. Breve (MIT): **60 s**. Todos los VUs
se reservan de antemano: `maxVUs = max(10, ⌈3 s × tasa⌉)`.

**Las tres restricciones** (`criteria.py`), las mismas en las dos ventanas:

1. **Éxito.** Más del 95 % de respuestas 2xx en la ventana medida.
2. **Pendientes.** Ninguna iteración descartada, y nunca todos los VUs
   ocupados a la vez. Con más peticiones en vuelo que tres segundos de
   llegadas, la latencia media pasa de 3 s (ley de Little).
3. **Latencia.** La **mediana** del último tercio no supera **1,5 veces** la
   del primero. Se usa la mediana y no el p95 porque, con carga sostenible, el
   p95 por tercios oscila mucho más. El umbral se justifica con la distribución
   del cociente en todas las corridas a tasas iguales o inferiores al MST final
   de cada perfil, pasen o no (`analyze.py`, «Umbral de latencia»): es el ruido
   con carga sostenible. Una cola que crece da cocientes muy por encima; con una
   sobrecarga del 1 %, el ratio pasa de 3 incluso en 60 s.

**Antes de cada corrida**, drenaje (la CPU del servicio por debajo del 10 % en
dos lecturas seguidas) y **60 s de descanso**. Cada corrida, también las de MIT,
empieza con la máquina en reposo. Es lo que da contenido al factor de ráfaga.

**La búsqueda** (`most.py`):

- **MIT primero.** Sin horquilla, se empieza en 1/s y se duplica hasta que una
  corrida no pasa; si 1/s no pasa, se divide entre dos hasta que una pasa (1:N
  lo necesitará). Con horquilla (la criba), se miden los dos extremos y se
  amplía por el lado que haga falta.
- **MST después, con la ventana larga, sin dar el MIT por cota.** Se miden
  0,8·MIT y MIT. Si 0,8·MIT no pasa, se baja (×0,8); si MIT pasa, se sube
  (×1,25). El MST puede salir mayor que el MIT, y el factor ≥ 1 es una
  medición, no algo impuesto por el procedimiento.
- **Bisección** entre la mayor tasa que pasa y la menor que no, hasta
  (hi − lo)/lo ≤ 5 %. Resolución: 0,01/s.
- **Confirmación.** Un valor solo vale si pasa **3 de 3**, contando la corrida
  de la búsqueda. Si falla una, se baja un 5 % y se vuelve a confirmar, como
  mucho dos veces.

**Precondiciones.** `most.py` se niega a empezar, y vuelve a comprobarlas antes
de cada corrida, si:

- el Mac va con batería (`pmset -g batt`);
- está activo el modo de bajo consumo (`pmset -g`);
- hay contenedores en marcha que no son de este compose.

Lo que no puede comprobar (aplicaciones abiertas, `caffeinate -i`) es
preparación manual; la presencia de `caffeinate` y la carga del anfitrión
(`uptime`) al principio y al final de cada corrida quedan en el `.meta.json`.

**Corrida inválida.** Se repite (hasta dos veces) y **no cuenta como fallo** de
ninguna restricción si `fr` se reinicia, si cambia el conjunto de workers de
uvicorn (uno muere y el supervisor lo sustituye) o si hay un OOM en su cgroup.

## Configuración de la campaña

| Parámetro | Valor | Por qué |
|---|---|---|
| Imagen | 480 × 640, `fixtures/` | Una sola, en el tramo de < 0,5 MP. El coste de MTCNN depende de los píxeles. |
| Perfil 1:1 | `verifyPair`, con un par de la misma persona | Existe ya, sin esperar a F8. |
| Workers × hilos × concurrencia | **1 × 1 × 8** (`w1t1c8`): la de mayor MST en la exploración (`results/exploration/`) | Una para todos los perfiles: con una por perfil, σ compararía configuraciones y no modelos. |
| Perfiles 1:N | `searchGallery` con `xs` y galerías de **5.000, 20.000 y 50.000** rostros | Varios tamaños para estimar `f(tamaño_galería)`; un solo modelo, porque a 480 × 640 los tres cuestan lo mismo dentro del error del método. |

## Los perfiles 1:N

TFM-MIS-MEM/docs#62. `searchGallery` contra una galería sembrada, sin recalibrar:
añadir la búsqueda a `k6/profile.js` es la única excepción a la congelación del
instrumento (`b6fff3d`), y lo que cambia en `most.py` es aditivo (la operación,
el tamaño, la siembra y lo que se anota), sin tocar la corrida, la búsqueda, la
confirmación ni la validez.

**La siembra** (`seed.py`, en el contenedor `seed`: la imagen de `fr`, en la red
`internal` y en los vCPU 0–1 de k6). Una vez por perfil, después de levantar el
banco y antes de la primera corrida:

- una galería `xs` creada por la API con la cuenta de k6 (`X-Account-Id
  load-most`), y una identidad enrolada por la API con `koch_2023`: la que tiene
  que coincidir con la imagen de k6 (`koch_2014`, sin `threshold`);
- el resto, hasta N rostros exactos, directamente en MongoDB: **un rostro por
  identidad**, códigos aleatorios de 192 B (`random.Random(N)`, reproducibles),
  construidos con las clases de persistencia del servicio y por lotes en el
  orden de `createIdentity`, de modo que la galería cumpla los invariantes de
  los contadores.

**La comprobación, que bloquea**, al empezar y otra vez al acabar el perfil: N
rostros y N identidades; `faceCount` e `identityCount` de la galería, N; cada
identidad con `faceCount` 1; el rostro sembrado con la misma forma BSON que el
enrolado y los dos por la vía rápida de `stored_code`; y una búsqueda con la
imagen de k6 que devuelve `200` con la identidad enrolada. Si falla la inicial,
el perfil no se mide; si falla la final, queda como no válido en
`results/search-status.json`, con el motivo. En los dos casos la secuencia sigue
con el perfil siguiente.

**Por qué basta una comprobación por perfil, y no una por corrida.** La galería
vive en MongoDB, no en `fr`: un reinicio de `fr` no la toca. La única
recreación dentro de un perfil es la de una corrida inválida (`up
--force-recreate fr`), que no recrea mongo; y aunque lo hiciera, compose
conserva su volumen anónimo. La búsqueda no escribe, y el `down` llega después
de la comprobación final. Íntegra al empezar, íntegra al acabar y con el mismo
`started_at` de mongo en todas las corridas, la galería es la misma en todas.
Si mongo cayera en mitad de una corrida, ésta contaría como «no pasa» y no como
inválida: la validez está congelada. Quedaría a la vista en el `.meta.json`.

**MongoDB entra en la medida por primera vez**, en los vCPU 0–1 que comparte con
k6 y con el fondo de la VM. Los 10 vCPU están repartidos; no hay uno libre para
él. Por eso se anota en cada corrida, antes y después (`service_before.mongo`,
`service_after.mongo`): `cpu.stat` de su cgroup, `started_at`, `restart_count`,
`oom_killed` y `serverStatus().opLatencies.reads` en crudo (latencia acumulada
en µs y operaciones). También el `cpu.stat` de `fr` (`service_*.cpu`). La vCPU
media es Δ`usage_usec` / Δ`sampled_at`, y abarca el calentamiento al 50 % y la
ventana.

**Criterio: un perfil lo limita MongoDB, y no `fr`**, si en las corridas que
fallan la latencia media de lectura de mongo (Δ`latency` / Δ`ops`) sube
claramente sobre la de la corrida a 1/s del mismo perfil, mientras la CPU de
`fr` se queda por debajo de la que alcanzó en el MST de 5.000. La interpretación
numérica es de `analyze.py`.

### Resultado de la campaña 1:N (`results/campaign/`, lanzada desde `5f48e92`)

| Rostros | MIT (/s) | MST (/s) | 1000/MST (ms) | Latencia a 1–2/s (ms) |
|---|---|---|---|---|
| 5.000 | 30,40 | 20,80 | 48,08 | 77,0 |
| 20.000 | 20,00 | 16,15 | 61,92 | 94,5 |
| 50.000 | 12,50 | 9,59 | 104,28 | 132,5 |

Las tablas completas, con `python3 load/analyze.py campaign`.

**El criterio, aplicado, no decide.** Se conserva tal como se escribió antes de
medir. No fija cifra para «claramente», y el veredicto depende del umbral u de
la subida de la lectura de mongo: a 50.000, MongoDB en 8 de 8 corridas que
fallan para u ≤ ×1,28, en 4 de 8 a ×1,5 y en ninguna por encima de ×1,60. Su
cláusula de CPU es falsa a 5.000 por construcción (la referencia, 2,094 vCPU,
es su propio MST) y a 50.000 se cumple con un margen de 0,016 vCPU. Un criterio
cuyo veredicto depende de un umbral que no fijó y de un margen de centésimas no
sirve para atribuir el límite.

**Por qué no lo limita MongoDB: una cota con lo medido.** En las MST confirmadas
a 50.000, cada búsqueda hace ~4,05 lecturas, que suman 25,5–26,7 ms, y gasta
24,9–26,0 ms de CPU de mongo por encima de su fondo. Con 8 búsquedas en vuelo,
por tiempo de lectura, mongo serviría ~300–313 búsquedas/s; pero su CPU por
búsqueda es casi igual a su tiempo de lectura (las lecturas son de CPU), así que
la cota que vale es la de sus 2 vCPU: **~77–80 búsquedas/s, ocho veces el MST
de 9,59/s**. En las corridas que fallan, la lectura sube de 4,74 ms a 6,08–7,56
ms: +5–11 ms por búsqueda, frente a los 104 ms por búsqueda en capacidad. Su
CPU no pasa de 0,43 vCPU (0,32 sin el fondo).

**Qué apunta al límite.** De 5.000 a 50.000, 1000/MST sube 56,2 ms, lo mismo que
la latencia aislada a 1 y 2/s (+56,5 y +54,6 ms); la pendiente en capacidad,
1,27 µs por rostro, es igual a la de latencia, 1,24 µs: la parte de f(n) no se
reparte entre las 8 inferencias en paralelo, se paga casi entera por búsqueda.
Con el MST de 50.000 en el extremo alto de su error (10,62/s), el incremento de
1000/MST sería de ~46 ms (46,1), el 83 % del de latencia a 1–2/s (82 % a 1/s,
84 % a 2/s): la conclusión se mantiene, porque repartida entre las inferencias
costaría una fracción mucho menor. Y la
CPU de `fr` en las corridas que fallan baja con n (2,89 → 2,36 → 1,84 vCPU de
media): el servicio se satura usando cada vez menos CPU. Las dos cosas apuntan a
una parte en serie dentro del proceso de `fr`. Que sea el bucle de eventos o el
GIL del único worker es una inferencia: confirmarlo exige medir con más
workers, y queda como trabajo futuro.

**La latencia a 4/s es más baja que a 1 y 2/s en los tres tamaños** (75,5, 85,3 y
112,8 ms; a 5.000, 55 ms a 8/s). Por eso f(n) en latencia se toma de las
corridas a 1 y 2/s. Hipótesis, no medida: con poca carga la CPU baja de
frecuencia y la latencia la paga.

**El swap del anfitrión** estuvo en ~4,2 GB durante toda la campaña 1:N (2,1 GB
al empezar la de verifyPair, el 12/10), y no creció en ninguna corrida. La 020
a 50.000 (10,09/s), que falló con 17 iteraciones descartadas, coincide con una
liberación de 1,5 GB (4.201 → 2.703 MB). Es la segunda bajada de escalón de ese
MST (la primera, de 10,62 a 10,09, la provocó la 017). Con dos bajadas, el error
del procedimiento en esa celda llega a ~15 % (5 % de la bisección más 5 % por
bajada), por encima del ~10 % declarado, que suponía una; el procedimiento
admite hasta dos.

## Uso

Precondiciones de la máquina: solo Docker, el editor y el chat abiertos;
enchufado; sin modo de bajo consumo; `caffeinate -i` en marcha y la pila de
desarrollo parada (`scripts/dev-down.sh` del workspace). Las órdenes, desde la
raíz del repositorio:

```sh
# Calibración: el servicio sintético, capacidad teórica de 10/s.
python3 load/most.py most --serie calibration --target calib

# Exploración, criba: MIT sin confirmar en la horquilla [5, 25].
python3 load/most.py most --serie exploration --only mit --bracket 5 25 --no-confirm \
    --workers 1 --threads 1 --concurrency 8

# Exploración: MST de las dos mejores, con el MIT de la criba.
python3 load/most.py most --serie exploration --only mst --mit 12.5 --no-confirm \
    --workers 4 --threads 1 --concurrency 1

# Deriva: 15 min a ~90 % del MIT de la configuración elegida, en tramos de un minuto.
python3 load/most.py drift --serie drift --rate 15.75 --window 900 --slices 15 \
    --workers 1 --threads 1 --concurrency 8

# Un perfil completo.
python3 load/most.py most --serie campaign --op verifyPair --model xs \
    --workers 1 --threads 1 --concurrency 8

python3 load/analyze.py
```

**La cadena nocturna** hace lo mismo sin intervención: criba de las siete
configuraciones (1 worker con 8×1, 4×2, 2×4 y 1×8 hilos × concurrencia; 2, 4 y
8 workers con 1×1) → MST de las dos de mayor MIT → deriva a 0,9·MIT de la de
mayor MST → MoST completo de verifyPair xs con ella. **La deriva es la
puerta**: si no cumple las tres restricciones, con tercios de 5 min, la cadena
termina sin medir xs. Una configuración que falla en la criba se anota y se
salta; una precondición incumplida detiene la cadena entera.

```sh
nohup caffeinate -i python3 load/most.py chain > load/results/chain.log 2>&1 &
cat load/results/chain-status.json    # estado, etapa, resultados y motivo de parada
tail -f load/results/chain.log        # una línea por corrida
```

**Los tres perfiles 1:N**, en secuencia y sin intervención: 5.000, 20.000 y
50.000 rostros, cada uno con su MoST completo en `w1t1c8`, serie `campaign`.
`seed` solo siembra y comprueba una galería, con sus tiempos.

```sh
nohup caffeinate -i python3 load/most.py search >> load/results/search.log 2>&1 < /dev/null &
cat load/results/search-status.json   # perfil en curso, resultados y motivos
python3 load/most.py seed --faces 50000
```

Python 3.9 del sistema, solo biblioteca estándar. `most.py` arranca y para el
compose; el secreto de `fr` lo genera en cada ejecución y no lo escribe en
ningún sitio.

## Resultados

`results/<serie>/<perfil>/<configuración>/NNN-<tipo>-<tasa>.json` es el resumen
de k6 sin tocar (`k6`), junto con los parámetros de la corrida (`params`). A su
lado, `.meta.json` guarda:

- el veredicto en el momento de medir;
- la validez;
- commit e imágenes;
- el estado de `fr` antes y después: procesos, reinicios, OOM y pico de memoria
  del cgroup; su `cpu.stat`, y el de MongoDB con sus lecturas (1:N, arriba);
- el del anfitrión al principio y al final: corriente, bajo consumo, `uptime`,
  swap usado (`sysctl vm.swapusage`) y otros contenedores. El crecimiento del
  swap durante la corrida queda en `swap_growth_mb`; no la invalida, pero se ve.

`analyze.py` no lee esos veredictos: vuelve a evaluar cada resumen con
`criteria.py`, y avisa si no coinciden. Además de MIT, MST y ráfaga, da por
perfil σ_S y σ_I (respecto a `detectFaces`), el coste por petición (1000/MST), las bajadas de escalón de la
confirmación, las inválidas y el crecimiento del swap; la descomposición
aproximada de `verifyPair` en dos detecciones más el resto; la distribución del
cociente de medianas que sostiene el umbral; y las corridas con cociente por
debajo de 0,8. Con los perfiles 1:N, además: f(n) con su ajuste lineal, los
recursos de cada corrida, el criterio de MongoDB en función del umbral y la cota
de MongoDB.

En los perfiles 1:N, junto a las corridas: `NNN-gallery-seed.json` (galería,
identidad enrolada y tiempos de la siembra), `NNN-gallery-start.json` y
`NNN-gallery-end.json` (las dos comprobaciones, con el estado de mongo). `NNN`
es el número de la corrida siguiente.
