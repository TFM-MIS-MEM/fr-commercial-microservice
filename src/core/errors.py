"""Manejador global de errores — patrón ERROR REPORT.

Todo error sale como `ErrorReport` ---`{code, message}` y `details` opcional---,
que es el esquema que declaran las tres especificaciones, con tipo de contenido
`application/json` y NUNCA `application/problem+json`.

Este fichero es el ÚNICO emisor del cuerpo de error del servicio. Es la razón de
que el rechazo de la identidad viva en una dependencia y no en un middleware: una
excepción lanzada desde un middleware no pasa por estos manejadores ---sale como
`500 text/plain`, medido en #33--- y para cumplir el esquema tendría que escribir
su propio cuerpo, que sería un segundo emisor. Las divergencias del cuerpo de
error de `auth` y `pricing` (#66, #79) nacieron exactamente de eso.

Tres reglas que el contrato no puede expresar y por eso se escriben aquí:

  1. La traza de pila no viaja en el cuerpo en ningún entorno. Va al registro, y
     solo cuando el error no es un camino previsto del contrato.
  2. Un error de validación es `400`, no el `422` que FastAPI emite por omisión:
     ninguna de las 27 operaciones del contrato declara `422`.
  3. Ningún error por debajo de 500 anuncia un error interno (#89). El código y
     el mensaje de los errores de la plataforma se derivan del propio estado,
     nunca de un valor de reserva.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.logger import get_logger

logger = get_logger(__name__)

#: Vocabulario de códigos por omisión, el mismo que `pricing` y `auth`.
#:
#: `ErrorReport` declara `code` como cadena libre, así que la especificación no
#: puede fijar este vocabulario y ninguna prueba de contrato lo protege. `405` es
#: la única entrada nueva: FastAPI lo emite ante un método no admitido sobre una
#: ruta existente y Express no, de modo que los otros dos servicios nunca lo han
#: necesitado.
DEFAULT_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "RATE_LIMIT_EXCEEDED",
}

INTERNAL_CODE = "INTERNAL_ERROR"
INTERNAL_MESSAGE = "Internal server error"


class ErrorReportError(Exception):
    """Error previsto por el contrato: se devuelve tal cual al cliente."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def code_for(status_code: int) -> str:
    """Código simbólico de un estado sin código propio.

    Para lo que no está en el vocabulario se usa el nombre del estado
    (`REQUEST_ENTITY_TOO_LARGE`) y no `INTERNAL_ERROR`: un 4xx etiquetado como
    error interno es justo el defecto de #89.
    """
    if status_code in DEFAULT_CODES:
        return DEFAULT_CODES[status_code]
    if status_code >= 500:
        return INTERNAL_CODE
    try:
        return HTTPStatus(status_code).name
    except ValueError:
        return "HTTP_ERROR"


def message_for(status_code: int) -> str:
    if status_code >= 500:
        return INTERNAL_MESSAGE
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Request rejected"


def error_report(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(body, status_code=status_code, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ErrorReportError)
    async def _domain(_request: Request, exc: ErrorReportError) -> JSONResponse:
        return error_report(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Los errores de Pydantic NO van al cuerpo: llevan `input`, que devuelve
        # al cliente lo que envió, y enlaces a la documentación de Pydantic, que
        # anuncian la pila del servicio. Van al registro.
        logger.info("request_validation_failed", path=request.url.path, errors=exc.errors())
        return error_report(400, DEFAULT_CODES[400], "Invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _platform(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Cubre TODA HTTPException ---la de FastAPI es subclase de ésta---: la
        # ruta inexistente, el método no admitido y cualquier estado futuro. El
        # `detail` no se devuelve: el código propio lanza `ErrorReportError`, y
        # un `detail` ajeno no es texto que este servicio haya decidido publicar.
        return error_report(
            exc.status_code,
            code_for(exc.status_code),
            message_for(exc.status_code),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", path=request.url.path, exc_info=exc)
        return error_report(500, INTERNAL_CODE, INTERNAL_MESSAGE)
