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
   p95 por tercios oscila mucho más. El umbral se justifica con la deriva
   medida en la configuración de la campaña (`results/drift/`): ruido frente a
   cola que crece. Con una sobrecarga del 1 %, el ratio pasa de 3 incluso en
   60 s.

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
| Workers × hilos × concurrencia | La de mayor MST en la exploración (`results/exploration/`) | Una para todos los perfiles: con una por perfil, σ compararía configuraciones y no modelos. |

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
python3 load/most.py drift --serie drift --rate 11.2 --window 900 --slices 15 \
    --workers 4 --threads 1 --concurrency 1

# Un perfil completo.
python3 load/most.py most --serie campaign --op verifyPair --model xs \
    --workers 4 --threads 1 --concurrency 1

python3 load/analyze.py
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
  del cgroup;
- el del anfitrión: corriente, bajo consumo, `uptime` y otros contenedores.

`analyze.py` no lee esos veredictos: vuelve a evaluar cada resumen con
`criteria.py`, y avisa si no coinciden.
