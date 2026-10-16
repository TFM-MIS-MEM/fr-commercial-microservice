"""Lo que comparten los controladores con estado: identificadores, `404` y `409`.

UN IDENTIFICADOR IMPOSIBLE ES UN RECURSO QUE NO EXISTE. Ninguna operación por
`galleryId`, `identityId` o `faceId` declara `400`: para el cliente, un
identificador que no puede existir es lo mismo que uno que no existe, y recibe
el mismo `404`.

EL `409` LO DA EL ÍNDICE QUE SALTÓ (`src/documents.conflict_code`), no una
consulta previa, que tendría una carrera entre la lectura y la escritura.
"""

from collections.abc import Callable

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from src.core.errors import ErrorReportError
from src.documents import conflict_code

#: Código de dominio de cada `409` -> mensaje.
CONFLICT_MESSAGES = {
    "GALLERY_NAME_ALREADY_EXISTS": "A gallery with that name already exists",
    "EXTERNAL_ID_ALREADY_EXISTS": "An identity with that externalId already exists in the gallery",
}

#: Mayor `skip` que admite MongoDB (entero de 64 bits). `page` no tiene
#: `maximum` en el contrato: una página que nadie puede alcanzar es una página
#: vacía, no un `500` por desbordamiento al codificar la consulta.
MAX_SKIP = 2**63 - 1


def not_found(resource: str) -> ErrorReportError:
    return ErrorReportError(404, "NOT_FOUND", f"{resource} not found")


def object_id(value: str, missing: Callable[[], ErrorReportError]) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise missing() from None


def conflict(exc: DuplicateKeyError) -> ErrorReportError:
    code = conflict_code(exc)
    if code is None:
        # Un índice único que no está en `CONFLICTS` no es un `409` declarado.
        raise exc
    return ErrorReportError(409, code, CONFLICT_MESSAGES[code])
