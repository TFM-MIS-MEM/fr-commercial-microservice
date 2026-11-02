# FR Commercial

Galerías, identidades, rostros e inferencia del SaaS de reconocimiento facial del
TFM. Python 3.11, FastAPI y MongoDB.

- **Contrato:** [`openapi/fr-commercial.yaml`](openapi/fr-commercial.yaml), 27
  operaciones. Es la fuente de verdad: si el código y el contrato discrepan, se
  corrige el código. `/docs` y `/openapi.json` se sirven **desde ese fichero**, no
  desde el código.
- **Estado:** 17 de las 27 operaciones implementadas.
  - Galerías, las cinco (F3, TFM-MIS-MEM/docs#35).
  - Identidades y rostros, las siete (F4, docs#36).
  - Inferencia sin estado: `detectFaces`, `verifyPair` y `extractEmbedding`
    (F5, docs#37).
  - Verificación contra identidad y búsqueda 1:N: `verifyAgainstIdentity` y
    `searchGallery` (F8, docs#40).
  - Embeddings guardados binarizados con LSSC (F6, docs#38).
  - **Plan de la cuenta con SPACE** (F10, docs#42 y docs#51): cada operación
    se decide con el plan que evalúa SPACE ---funcionalidades, modelos, límites
    de galerías y rostros, congelación de galerías calculada al vuelo--- y las
    que consumen imputan sus unidades en SPACE al admitirse. Con SPACE caído,
    `503 PLAN_UNAVAILABLE`. Véase [`src/core/plan.py`](src/core/plan.py).
  - **Fuera:** los puntos de notificación (webhooks, F13, siete operaciones),
    la prueba de vida (F12, dos) y los tokens de enrolamiento (F11, una). Del
    token de procedencia (F7, docs#39) solo está la emisión en
    `extractEmbedding`: nadie lo verifica todavía, y las entradas por vector
    responden `400 EMBEDDING_INPUT_NOT_SUPPORTED`. El evento de consumo hacia
    `pricing` (docs#138) tampoco está.
  - Alcanzable a través de la pasarela en `/v1/fr/commercial`, con los dos
    canales (TFM-MIS-MEM/api-gateway#3).
- **Medición de capacidad:** el instrumento de carga y los resultados de la
  campaña viven en [`load/`](load/README.md) (docs#120, docs#62).

## Lo que hay que saber antes de tocarlo

**El servicio confía en la identidad que escribe la pasarela** (`X-Account-Id`,
`X-Auth-Channel`, `X-Role`) y **no verifica credencial alguna**. Las tres
cabeceras se exigen completas, con valores cerrados y sin repetir; si no, `401`.
La garantía de que nadie las falsifica es de red: el compose de producción no
publica ningún puerto, y el de desarrollo solo en `127.0.0.1`. Razonado en
[`src/core/identity.py`](src/core/identity.py).

**Configuración sin valores por omisión.** `MONGODB_URI` y
`EMBEDDING_TOKEN_SECRET` son obligatorias, en el código y en el compose de
producción. Véase [`src/core/settings.py`](src/core/settings.py).

**Modelos.** MTCNN y tres EdgeFace (`xs`, `s`, `base`), en CPU, cargados al
arrancar desde [`checkpoints/`](checkpoints/README.md); sin ellos no arranca.
Procedencia y licencias en [`src/inference/README.md`](src/inference/README.md):
**los pesos de EdgeFace son CC BY-NC-SA 4.0, no de uso comercial.**

**Todo error sale como `ErrorReport`** (`{code, message, details?}`), desde un
único sitio: [`src/core/errors.py`](src/core/errors.py). Validación es `400`,
nunca `422`.

**Cliente de MongoDB: `pymongo.AsyncMongoClient`, no `motor`.** Es una
desviación deliberada de `python-template`: `motor` está deprecado desde el
14/05/2026 (TFM-MIS-MEM/docs#107).

## Ejecutar

```sh
docker network create tfm-network          # solo la primera vez
docker compose -f docker-compose.dev.yml up --build
```

| | |
|---|---|
| Servicio (directo, sin pasarela) | `http://127.0.0.1:3002` · `/health` · `/docs` |
| MongoDB | `mongodb://127.0.0.1:27019` |

Sin pasarela, las operaciones exigen poner las cabeceras a mano: es la forma
correcta de probarlas, porque el servicio confía en ellas.

## Pruebas

```sh
make test
```

En el **contenedor de desarrollo**, no en el anfitrión: `torch==2.14.1+cpu` solo
existe para linux. El objetivo levanta el MongoDB del compose y fija
`TEST_MONGODB_URI`, que es un **servidor**: cada sesión crea su propia base y la
borra. Sin ella, las pruebas que necesitan base fallan en lugar de saltarse.

## Dependencias

`requirements.in` y `requirements-dev.in` declaran las directas;
`requirements.txt` y `requirements-dev.txt` son el árbol completo, fijado y con
resúmenes (`make lock`). La imagen instala `requirements.txt` con
`--require-hashes`, y la CI audita **ese** fichero (`make audit`): lo que se
despliega, no las herramientas de desarrollo.

`torch` y `torchvision` vienen del índice CPU de PyTorch (`+cpu`): el de PyPI
arrastra CUDA también en aarch64 (imagen de 9,31 GB frente a 1,58 GB).
`pip-audit` no los encuentra en PyPI y los salta; la CI los audita en un paso
aparte por su versión sin la etiqueta local. `make lock` tiene que ejecutarse en
linux, por la misma razón que las pruebas.

## Licencias

Este repositorio incluye pesos y código de terceros, cada uno con su licencia.
El detalle fichero a fichero, con commits de origen y resúmenes, está en
[`checkpoints/README.md`](checkpoints/README.md) y
[`src/inference/README.md`](src/inference/README.md).

**Pesos de EdgeFace** (`checkpoints/edgeface_*.pt`): **CC BY-NC-SA 4.0**,
[`checkpoints/LICENSE-EdgeFace-weights`](checkpoints/LICENSE-EdgeFace-weights).
Autoría de Anjith George, Christophe Ecabert, Hatef Otroshi Shahreza, Ketan
Kotwal y Sébastien Marcel (Idiap Research Institute), publicados en
[otroshi/edgeface](https://github.com/otroshi/edgeface). Se redistribuyen sin
modificar, byte a byte los del proyecto original.
[Resumen de la licencia](https://creativecommons.org/licenses/by-nc-sa/4.0/).

- **Uso no comercial.** Valen para este trabajo académico; un despliegue
  comercial necesitaría otros pesos o una licencia de sus titulares.
- Cualquier redistribución conserva esta atribución y el enlace al proyecto
  original, y una obra derivada de los pesos se publica con la misma licencia.

**Pesos de MTCNN** (`checkpoints/mtcnn_*.npz`): **MIT**,
[`checkpoints/LICENSE-MTCNN-weights`](checkpoints/LICENSE-MTCNN-weights).
Proceden de
[kpzhang93/MTCNN_face_detection_alignment](https://github.com/kpzhang93/MTCNN_face_detection_alignment),
convertidos de `.npy` a `.npz` sin cambiar ningún tensor.

**Código tomado de fr-compilation** (`src/inference/edgeface/` y
`src/inference/mtcnn/`). fr-compilation no tiene licencia propia; cada pieza
conserva la del proyecto del que procede, y su texto va junto al código:

| Código | Proyecto original | Licencia |
|---|---|---|
| `src/inference/edgeface/` | [otroshi/edgeface](https://github.com/otroshi/edgeface) | BSD-3-Clause, [`LICENSE`](src/inference/edgeface/LICENSE) |
| `src/inference/mtcnn/detector.py`, `align.py` | [mk-minchul/AdaFace](https://github.com/mk-minchul/AdaFace) | MIT, [`LICENSE-AdaFace`](src/inference/mtcnn/LICENSE-AdaFace) |
| `src/inference/mtcnn/nets.py`, `box_utils.py`, `first_stage.py` | [TropComplique/mtcnn-pytorch](https://github.com/TropComplique/mtcnn-pytorch) | MIT, [`LICENSE-mtcnn-pytorch`](src/inference/mtcnn/LICENSE-mtcnn-pytorch) |

`src/inference/lssc.py` no es una copia: reimplementa el método de
binarización de fr-compilation, con los cortes calculados en este servicio.
