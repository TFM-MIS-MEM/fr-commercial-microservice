"""Identidad propagada por la pasarela.

Traducción a FastAPI de la implementación de referencia de `pricing`
(`src/middlewares/auth.middleware.js`, #65), que es donde está razonado por qué
el servicio confía en `X-Account-Id`, `X-Auth-Channel` y `X-Role` sin verificar
credencial alguna. No se repite aquí: se escribe lo que cambia al traducirla, que
se midió en #33 antes de escribir una línea.

DÓNDE VIVE EL RECHAZO: EN UNA DEPENDENCIA, NO EN UN MIDDLEWARE
--------------------------------------------------------------
Un middleware corre fuera de la capa de excepciones de FastAPI. Si lanza el
error de dominio, el manejador de `src/core/errors.py` no lo ve y la petición
sale como `500 text/plain`; con el manejador genérico, como `500` en JSON.
Ninguna de las 27 operaciones declara `500`. Para cumplir, el middleware tendría
que escribir su propio cuerpo: un segundo emisor de `ErrorReport`.

La dependencia corre dentro de esa capa. Lanza `ErrorReportError` y la atiende
el mismo manejador que a cualquier otro error: un solo emisor. Se monta por
router, de modo que `/health`, `/docs` y `/openapi.json` quedan fuera por
estructura y no por una lista de rutas exentas. Y admite política por operación,
que el contrato va a exigir: cinco operaciones aceptan además un token de
enrolamiento que la pasarela no resuelve, y dos solo el canal de API.

TRES DIFERENCIAS CON EXPRESS, LAS TRES MEDIDAS
----------------------------------------------
1. Se leen las cabeceras con `request.headers`, NO con parámetros `Header()`.
   Con `Header()` la ausencia sale como `422` ---no declarado---, se acepta un
   `X-Account-Id` en blanco, se aceptan duplicados, y las tres cabeceras
   aparecen como parámetros en el `/openapi.json` generado, que el contrato no
   declara porque son internas entre la pasarela y el servicio.

2. Duplicados. `request.headers.get()` de Starlette devuelve la PRIMERA: con
   `X-Role: ADMIN` y `X-Role: CUSTOMER` el rol resultante era `ADMIN`. Aquí se
   exige exactamente un valor por cabecera. Express une los duplicados con
   `", "` y los rechaza solo por accidente ---la unión cae fuera del conjunto---,
   así que un `X-Account-Id` duplicado le pasa como la cuenta `"acc1, acc2"`:
   TFM-MIS-MEM/docs#101. Este servicio es, en eso, más estricto que la
   referencia.

3. Orden respecto al cuerpo. FastAPI lee y parsea el cuerpo ANTES de resolver
   las dependencias. Un JSON roto sale como `400` antes del `401`, igual que en
   Express, donde `express.json()` va delante del middleware. Pero una
   operación que declare un fichero con `UploadFile = File(...)` lo lee entero
   ---y lo vuelca a disco por encima de 1 MiB--- antes de rechazar la
   identidad: medido, 50 MiB leídos y volcados para acabar en `401`. Por el
   camino legítimo no ocurre, porque la pasarela corta antes; es el coste que
   hereda F5 (véase ESTADO, sesión 9). El orden es el mismo que en Express; el
   coste, no: `express.json()` corta en 100 kB por omisión ---10 MB en `auth` y
   `pricing`, que lo suben---, y aquí se leyeron 50 MiB en memoria antes del
   `401`, sin tope.

LO QUE NO SE HACE
-----------------
Ni valores por omisión, ni respaldo a ningún token, ni identidad deducida de
nada que no sean estas tres cabeceras. Atribuir un rol o un canal a una petición
que no lo declara es inventar identidad. Se responde `401`, nunca `403`: no hay
credencial válida a la que negarle un permiso, no hay identidad en absoluto. Y el
mensaje es el mismo en todos los casos: a quien sondea no se le dice qué falta.
"""

from dataclasses import dataclass

from fastapi import Request

from src.core.errors import ErrorReportError

CHANNELS = frozenset({"frontend", "api"})
ROLES = frozenset({"CUSTOMER", "ADMIN"})

ACCOUNT_HEADER = "x-account-id"
CHANNEL_HEADER = "x-auth-channel"
ROLE_HEADER = "x-role"


@dataclass(frozen=True)
class Identity:
    """Todo se vincula a la cuenta, nunca a un usuario (CLAUDE.md, regla 3)."""

    account_id: str
    channel: str
    role: str


def _reject() -> ErrorReportError:
    return ErrorReportError(401, "UNAUTHORIZED", "No valid identity was provided")


def _single(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else None


async def require_identity(request: Request) -> Identity:
    """Exige la identidad completa, con valores cerrados, o rechaza con 401.

    FastAPI cachea el resultado por petición: montarla en el router y pedirla
    además como parámetro del manejador la evalúa una sola vez.
    """
    account_id = _single(request, ACCOUNT_HEADER)
    channel = _single(request, CHANNEL_HEADER)
    role = _single(request, ROLE_HEADER)

    if account_id is None or account_id.strip() == "":
        raise _reject()
    if channel not in CHANNELS or role not in ROLES:
        raise _reject()

    return Identity(account_id=account_id, channel=channel, role=role)
