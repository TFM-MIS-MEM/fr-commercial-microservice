# FR Commercial

Galerías, identidades, rostros e inferencia del SaaS de reconocimiento facial del
TFM. Python 3.11, FastAPI y MongoDB.

- **Contrato:** [`openapi/fr-commercial.yaml`](openapi/fr-commercial.yaml), 27
  operaciones. Es la fuente de verdad: si el código y el contrato discrepan, se
  corrige el código. `/docs` y `/openapi.json` se sirven **desde ese fichero**, no
  desde el código.
- **Estado:** bootstrap (TFM-MIS-MEM/docs#33). Implementada `getGallery`.

## Lo que hay que saber antes de tocarlo

**El servicio confía en la identidad que escribe la pasarela** (`X-Account-Id`,
`X-Auth-Channel`, `X-Role`) y **no verifica credencial alguna**. Las tres
cabeceras se exigen completas, con valores cerrados y sin repetir; si no, `401`.
La garantía de que nadie las falsifica es de red: el compose de producción no
publica ningún puerto, y el de desarrollo solo en `127.0.0.1`. Razonado en
[`src/core/identity.py`](src/core/identity.py).

**Configuración sin valores por omisión.** `MONGODB_URI` es obligatoria, en el
código y en el compose de producción. Véase
[`src/core/settings.py`](src/core/settings.py).

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
make install
TEST_MONGODB_URI=mongodb://127.0.0.1:27019 make test
```

`TEST_MONGODB_URI` es un **servidor**; cada sesión crea su propia base y la borra.
Sin ella, las pruebas que necesitan base fallan en lugar de saltarse.

## Dependencias

`requirements.in` y `requirements-dev.in` declaran las directas;
`requirements.txt` y `requirements-dev.txt` son el árbol completo, fijado y con
resúmenes (`make lock`). La imagen instala `requirements.txt` con
`--require-hashes`, y la CI audita **ese** fichero (`make audit`): lo que se
despliega, no las herramientas de desarrollo.
