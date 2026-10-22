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
saltó (`src/controllers/common.conflict`). La consulta previa tendría una
carrera entre la lectura y la escritura.
"""

import asyncio

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from src.controllers.common import MAX_SKIP, conflict, not_found, object_id
from src.core.errors import ErrorReportError
from src.core.identity import Identity
from src.core.plan import Plan
from src.documents.base import DELETING, utc_now
from src.documents.face import COLLECTION as FACES
from src.documents.gallery import COLLECTION, GalleryDocument
from src.documents.identity import COLLECTION as IDENTITIES
from src.models.gallery import Gallery, GalleryCreate, GalleryPage, GalleryUpdate, ModelName

#: Sin `model`, el contrato dice «el modelo por defecto del plan». `xs` está en
#: los tres planes de la tarifa: es el modelo por defecto de todos.
DEFAULT_MODEL = ModelName.xs


def _not_found() -> ErrorReportError:
    return not_found("Gallery")


def _object_id(gallery_id: str) -> ObjectId:
    return object_id(gallery_id, _not_found)


async def create_gallery(
    db: AsyncDatabase, identity: Identity, plan: Plan, body: GalleryCreate
) -> Gallery:
    """STATE CREATION OPERATION: siempre `active`, contadores a 0.

    EL PLAN (F10): el modelo tiene que estar en él (`MODEL_NOT_IN_PLAN`) y la
    cuenta por debajo de su límite de galerías (`GALLERY_LIMIT_REACHED`). El
    límite es de ESTADO: se cuenta lo que hay, con los documentos de este
    servicio, y SPACE solo da el valor. Dos altas simultáneas con la cuenta a una
    galería del límite pueden pasar las dos; se acepta.
    """
    model = body.model if "model" in body.model_fields_set else DEFAULT_MODEL
    plan.require_model(model.value)
    galleries = await db[COLLECTION].count_documents({"accountId": identity.account_id})
    if galleries >= plan.limits["maxGalleries"]:
        raise ErrorReportError(
            403,
            "GALLERY_LIMIT_REACHED",
            "The plan does not allow more galleries",
            {"limit": int(plan.limits["maxGalleries"])},
        )
    doc = GalleryDocument(
        accountId=identity.account_id,
        name=body.name,
        description=body.description,
        model=model,
    )
    try:
        result = await db[COLLECTION].insert_one(doc.to_document())
    except DuplicateKeyError as exc:
        raise conflict(exc) from None
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
        raise conflict(exc) from None
    if doc is None:
        raise _not_found()
    return Gallery.from_document(GalleryDocument.model_validate(doc))


async def delete_gallery(db: AsyncDatabase, identity: Identity, gallery_id: str) -> None:
    """STATE TRANSITION OPERATION, en cascada. Permitida sobre una congelada.

    SIN TRANSACCIONES (el MongoDB de este servicio es un nodo suelto), así que
    importa el ORDEN: marca, rostros, identidades y la galería AL FINAL. Si el
    proceso cae a mitad, la galería sigue existiendo y visible ---con los
    contadores desfasados---, y repetir el `DELETE` termina el trabajo. En el
    orden inverso quedarían identidades y rostros huérfanos que ninguna
    operación alcanza. Lo prueba `tests/contract/test_delete_gallery.py`, con
    una caída inyectada en cada uno de los dos últimos pasos.

    LA MARCA DE BORRADO (`deletingAt`) cierra la carrera con las altas de F4
    (TFM-MIS-MEM/docs#36). Sin ella, un `createIdentity` o un `addFace` que
    hubiera leído la galería antes podía insertar DESPUÉS de los `delete_many`
    y contarse en la galería ANTES de su `delete_one`: respondía `201` y dejaba
    datos biométricos de una galería borrada. Con ella, el `$inc` de las altas
    filtra la marca (`src/documents/base.LIVE`): o llega antes de la marca, y entonces sus
    inserciones son anteriores a los `delete_many` y se borran con todo lo
    demás; o no encuentra la galería, deshace lo que insertó y responde `404`
    (`src/controllers/identities`). Lo prueba `tests/contract/test_races.py`
    forzando ese entrelazado.

    La marca sustituye a la lectura de propiedad: si no casa, la galería no
    existe o es de otra cuenta, y no se ha escrito nada. Tras una caída la
    galería marcada sigue visible y repetir el `DELETE` termina; mientras
    tanto, las altas sobre ella dan `404`.
    """
    oid = _object_id(gallery_id)
    owner = {"_id": oid, "accountId": identity.account_id}
    marked = await db[COLLECTION].update_one(owner, {"$set": {DELETING: utc_now()}})
    if marked.matched_count == 0:
        raise _not_found()

    await db[FACES].delete_many({"galleryId": oid})
    await db[IDENTITIES].delete_many({"galleryId": oid})
    await db[COLLECTION].delete_one(owner)
