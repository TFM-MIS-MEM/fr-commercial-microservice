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

Vale igual para `updateGallery` y `deleteGallery`: la cuenta va en el filtro de
la escritura, y una galería ajena es, también aquí, una galería que no existe.

LA UNICIDAD DEL NOMBRE la garantiza el índice, no una consulta previa: se
escribe sin comprobar y el `DuplicateKeyError` se traduce según el índice que
saltó (`src/documents.conflict_code`). La consulta previa tendría una carrera
entre la lectura y la escritura.
"""

import asyncio

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from src.core.errors import ErrorReportError
from src.core.identity import Identity
from src.documents import conflict_code
from src.documents.base import utc_now
from src.documents.face import COLLECTION as FACES
from src.documents.gallery import COLLECTION, GalleryDocument
from src.documents.identity import COLLECTION as IDENTITIES
from src.models.gallery import Gallery, GalleryCreate, GalleryPage, GalleryUpdate, ModelName

#: PROVISIONAL HASTA F10 (TFM-MIS-MEM/docs#42). El contrato dice que, sin
#: `model`, se usa «el modelo por defecto del plan», y F3 no tiene plan
#: conectado. `xs` está en los tres planes, así que nunca crea algo que el plan
#: prohíba. Por lo mismo, F3 NO emite los `403` que `createGallery` declara
#: (`MODEL_NOT_IN_PLAN`, `GALLERY_LIMIT_REACHED`): hasta F10 se puede crear una
#: galería `base` en una cuenta Free, y como no hay `plan_changed` que la
#: congele, F10 tendrá que reconciliar las que se hayan creado antes.
DEFAULT_MODEL = ModelName.xs

MESSAGES = {"GALLERY_NAME_ALREADY_EXISTS": "A gallery with that name already exists"}

#: Mayor `skip` que admite MongoDB (entero de 64 bits). `page` no tiene
#: `maximum` en el contrato: una página que ninguna cuenta puede alcanzar es una
#: página vacía, no un `500` por desbordamiento al codificar la consulta.
MAX_SKIP = 2**63 - 1


def _not_found() -> ErrorReportError:
    return ErrorReportError(404, "NOT_FOUND", "Gallery not found")


def _object_id(gallery_id: str) -> ObjectId:
    try:
        return ObjectId(gallery_id)
    except (InvalidId, TypeError):
        # Ninguna operación por `galleryId` declara `400`: un identificador que
        # no puede existir es, para el cliente, una galería que no existe.
        raise _not_found() from None


def _conflict(exc: DuplicateKeyError) -> ErrorReportError:
    code = conflict_code(exc)
    if code is None:
        # Un índice único que no está en `CONFLICTS` no es un `409` declarado.
        raise exc
    return ErrorReportError(409, code, MESSAGES[code])


async def create_gallery(db: AsyncDatabase, identity: Identity, body: GalleryCreate) -> Gallery:
    """STATE CREATION OPERATION: siempre `active`, contadores a 0."""
    model = body.model if "model" in body.model_fields_set else DEFAULT_MODEL
    doc = GalleryDocument(
        accountId=identity.account_id,
        name=body.name,
        description=body.description,
        model=model,
    )
    try:
        result = await db[COLLECTION].insert_one(doc.to_document())
    except DuplicateKeyError as exc:
        raise _conflict(exc) from None
    doc.id = result.inserted_id
    return Gallery.from_document(doc)


async def list_galleries(
    db: AsyncDatabase, identity: Identity, page: int, page_size: int
) -> GalleryPage:
    """RETRIEVAL OPERATION, paginada.

    ORDEN: `_id` ascendente. Es único, así que el orden es total y la misma
    consulta da siempre las mismas páginas; y una galería nueva cae al final, de
    modo que crear no desplaza las páginas ya leídas (borrar sí). Con `name`
    también desplazaría renombrar y crear. El precio, medido en #35: el índice
    `{accountId, name}` sirve el filtro pero no este orden, y el plan es `SORT`
    en memoria sobre las galerías de la cuenta, que son pocas (1 en Free, 5 en
    Pro).

    `total` sale de un `count_documents` lanzado EN PARALELO con la página
    (`COUNT_SCAN` sobre el prefijo del mismo índice, sin leer documentos). No es
    una foto atómica de las dos: con escrituras concurrentes puede diferir en
    una. Consistencia aceptada.
    """
    query = {"accountId": identity.account_id}
    skip = (page - 1) * page_size
    count = db[COLLECTION].count_documents(query)
    if skip > MAX_SKIP:
        docs, total = [], await count
    else:
        cursor = db[COLLECTION].find(query).sort("_id", ASCENDING).skip(skip).limit(page_size)
        docs, total = await asyncio.gather(cursor.to_list(), count)
    return GalleryPage(
        items=[Gallery.from_document(GalleryDocument.model_validate(d)) for d in docs],
        page=page,
        pageSize=page_size,
        total=total,
    )


async def get_gallery(db: AsyncDatabase, identity: Identity, gallery_id: str) -> Gallery:
    """RETRIEVAL OPERATION: la cuenta propietaria forma parte del filtro."""
    oid = _object_id(gallery_id)

    doc = await db[COLLECTION].find_one({"_id": oid, "accountId": identity.account_id})
    if doc is None:
        raise _not_found()
    # Un documento corrupto no se sirve: `ValidationError`, y `500`. El porqué,
    # en src/documents/gallery.py.
    return Gallery.from_document(GalleryDocument.model_validate(doc))


async def update_gallery(
    db: AsyncDatabase, identity: Identity, gallery_id: str, body: GalleryUpdate
) -> Gallery:
    """STATE TRANSITION OPERATION sobre `name` y `description`.

    - Un cuerpo VACÍO es válido (el contrato no declara `minProperties`) y no
      escribe nada: `200` con la galería tal cual, sin tocar `updatedAt`.
    - Una galería CONGELADA se puede editar. `updateGallery` no declara ningún
      `403` de dominio; la congelación bloquea enrolar, verificar y buscar, no
      los metadatos.
    - Renombrar con su propio nombre no es un conflicto: el índice no choca con
      el mismo documento.
    """
    oid = _object_id(gallery_id)
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    if not changes:
        return await get_gallery(db, identity, gallery_id)

    changes["updatedAt"] = utc_now()
    try:
        doc = await db[COLLECTION].find_one_and_update(
            {"_id": oid, "accountId": identity.account_id},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise _conflict(exc) from None
    if doc is None:
        raise _not_found()
    return Gallery.from_document(GalleryDocument.model_validate(doc))


async def delete_gallery(db: AsyncDatabase, identity: Identity, gallery_id: str) -> None:
    """STATE TRANSITION OPERATION, en cascada. Permitida sobre una congelada.

    SIN TRANSACCIONES (el MongoDB de este servicio es un nodo suelto), así que
    importa el ORDEN: rostros, identidades y la galería AL FINAL. Si el proceso
    cae a mitad, la galería sigue existiendo y visible ---con los contadores
    desfasados---, y repetir el `DELETE` termina el trabajo. En el orden inverso
    quedarían identidades y rostros huérfanos que ninguna operación alcanza.
    Lo prueba `tests/contract/test_delete_gallery.py`, con una caída inyectada
    en cada uno de los dos últimos pasos.

    QUEDA ABIERTA una carrera con F4: un `addFace` concurrente puede insertar un
    rostro entre el borrado de los rostros y el de la galería. Anotada en
    TFM-MIS-MEM/docs#36.
    """
    oid = _object_id(gallery_id)
    owner = {"_id": oid, "accountId": identity.account_id}
    if await db[COLLECTION].find_one(owner, projection={"_id": 1}) is None:
        raise _not_found()

    await db[FACES].delete_many({"galleryId": oid})
    await db[IDENTITIES].delete_many({"galleryId": oid})
    await db[COLLECTION].delete_one(owner)
