"""Galerías.

POR QUÉ 404 Y NO 403 ANTE LA GALERÍA DE OTRA CUENTA ---también si quien pregunta
es ADMIN---:

  1. El contrato no deja otra salida. `getGallery` declara `403` solo como
     `ForbiddenAccountSuspended`, que emite la pasarela; un `403` del servicio
     por «no es tuya» sería una respuesta con un significado que la operación no
     declara.
  2. Un `403` confirma que la galería existe. Con identificadores que se pueden
     probar, eso es un oráculo de qué galerías tienen las demás cuentas. El `404`
     es idéntico para la galería ajena y para la inexistente, y hay prueba de
     que lo es.
  3. `ADMIN` no amplía el alcance aquí. La propiedad se decide por `accountId`
     (CLAUDE.md, regla 3), y el contrato de este servicio no tiene ninguna
     operación de administración: el rol `ADMIN` gobierna la gestión de cuentas
     en `auth`, no el acceso a los datos biométricos de otros clientes. Si algún
     día hiciera falta, sería una operación nueva, declarada en el contrato, y no
     un atajo en ésta.
"""

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from src.core.errors import ErrorReportError
from src.core.identity import Identity
from src.documents.gallery import COLLECTION, GalleryDocument
from src.models.gallery import Gallery


def _not_found() -> ErrorReportError:
    return ErrorReportError(404, "NOT_FOUND", "Gallery not found")


async def get_gallery(db: AsyncDatabase, identity: Identity, gallery_id: str) -> Gallery:
    """RETRIEVAL OPERATION: la cuenta propietaria forma parte del filtro."""
    try:
        oid = ObjectId(gallery_id)
    except (InvalidId, TypeError):
        # `getGallery` no declara `400`: un identificador que no puede existir
        # es, para el cliente, una galería que no existe.
        raise _not_found() from None

    doc = await db[COLLECTION].find_one({"_id": oid, "accountId": identity.account_id})
    if doc is None:
        raise _not_found()
    # Un documento corrupto no se sirve: `ValidationError`, y `500`. El porqué,
    # en src/documents/gallery.py.
    return Gallery.from_document(GalleryDocument.model_validate(doc))
