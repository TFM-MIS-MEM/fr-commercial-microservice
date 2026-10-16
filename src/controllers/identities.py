"""Identidades y rostros (F4, #36), con el rostro guardado como código LSSC (F6, #38).

LA CADENA DE PERTENENCIA. La galería es de la cuenta; la identidad, de la
galería; el rostro, de la identidad. Un eslabón roto es `404`, y el mensaje
nombra el PRIMERO que se rompe. Un identificador imposible también es `404`
(`src/controllers/common`). Una galería de otra cuenta es una galería que no
existe, también para `ADMIN` (el porqué, en `src/controllers/galleries.py`), y
una identidad de otra galería de la misma cuenta es una identidad que no existe
aquí. `Identity` no lleva `accountId` (CLAUDE.md): la galería hay que leerla
igualmente.

LAS ALTAS (`createIdentity`, `addFace`) comprueban en este orden, y la
inferencia va antes de cualquier escritura:

  1. Lo que no necesita estado: entradas por vector (`400
     EMBEDDING_INPUT_NOT_SUPPORTED` hasta F7, porque aceptarlas sin verificar el
     token sería admitir vectores de procedencia desconocida) y la imagen que
     falta (`400`).
  2. La cadena (`404`) y la congelación (`403 GALLERY_FROZEN_MODEL_NOT_IN_PLAN`).
     El `403` depende solo del estado guardado, no del plan, y el contrato lo
     declara; por eso lo emite ya F4 y no espera a F10. Las bajas y
     `updateIdentity` se permiten sobre una congelada: no declaran `403` de
     dominio, y congelar bloquea enrolar, verificar y buscar (criterio de F3).
  3. La inferencia: rostro de mayor confianza con el modelo DE LA GALERÍA, y su
     código LSSC de 192 B. `INVALID_IMAGE`, `IMAGE_TOO_LARGE` y
     `NO_FACE_DETECTED` como en F5.
  4. Las escrituras. Un `externalId` repetido se descubre al insertar, después
     de inferir: es el precio de escribir sin comprobar antes.

SIN TRANSACCIONES: LA REGLA DE LOS CONTADORES. Ninguna alta deja un contador
por encima de lo que hay; ninguna baja deja `faceCount` de la identidad por
encima. En las altas, cada `$inc` va DESPUÉS de insertar. En las bajas, el
`faceCount` de la identidad se decrementa ANTES de borrar el rostro, y es el
que protege el invariante «una identidad no se queda sin rostros»; los de la
galería se decrementan con lo realmente borrado, justo después de cada borrado,
y una caída en medio puede dejarlos por encima. Esos desfases de la galería son
de la reconciliación y no se prueban. El orden de cada operación, y lo que
deja una caída en cada punto, en su docstring. Los cambios de contador no tocan
`updatedAt`: solo lo cambia `updateIdentity`.

LAS CARRERAS, cerradas con la marca de borrado (`src/documents/base.DELETING`):
`deleteGallery` marca la galería y `deleteIdentity` la identidad antes de
borrar nada, y el `$inc` de cada alta filtra la marca. Si no encuentra lo que
cuenta, deshace lo que insertó y responde `404`: no quedan datos biométricos
de algo borrado. Dos `deleteFace` concurrentes no dejan la identidad sin rostros
porque el `409 LAST_FACE_OF_IDENTITY` sale de un decremento condicional, no de
una lectura previa. Los entrelazados, forzados, en `tests/contract/test_races.py`.
"""

import asyncio

from bson import ObjectId
from fastapi import UploadFile
from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from src.controllers.common import MAX_SKIP, conflict, not_found, object_id
from src.controllers.inference import Inference, best_face, run
from src.core.errors import ErrorReportError
from src.core.identity import Identity as Caller
from src.documents.base import DELETING, LIVE, utc_now
from src.documents.face import COLLECTION as FACES
from src.documents.face import FaceDocument
from src.documents.gallery import COLLECTION as GALLERIES
from src.documents.identity import COLLECTION as IDENTITIES
from src.documents.identity import IdentityDocument
from src.inference import lssc
from src.models.face import AddFaceForm, Face, FaceSource
from src.models.gallery import GalleryStatus
from src.models.identity import CreateIdentityForm, Identity, IdentityPage, IdentityUpdate

VECTOR_PARTS = ("embedding", "embeddingToken")


def _gallery_missing() -> ErrorReportError:
    return not_found("Gallery")


def _identity_missing() -> ErrorReportError:
    return not_found("Identity")


def _face_missing() -> ErrorReportError:
    return not_found("Face")


def _frozen() -> ErrorReportError:
    return ErrorReportError(
        403,
        "GALLERY_FROZEN_MODEL_NOT_IN_PLAN",
        "The gallery is frozen: its model is not available in the current plan",
    )


def _last_face() -> ErrorReportError:
    return ErrorReportError(
        409,
        "LAST_FACE_OF_IDENTITY",
        "An identity cannot be left without faces: add the new one first",
    )


# ------------------------------------------------------------------ la cadena


async def _gallery(
    db: AsyncDatabase, caller: Caller, gallery_id: str, *, enrolling: bool = False
) -> dict:
    """La galería de la cuenta, o `404`. Para enrolar, además sin marca de
    borrado (`404`) y sin congelar (`403`)."""
    query = {"_id": object_id(gallery_id, _gallery_missing), "accountId": caller.account_id}
    if enrolling:
        query |= LIVE
    doc = await db[GALLERIES].find_one(query, projection={"model": 1, "status": 1})
    if doc is None:
        raise _gallery_missing()
    if enrolling and doc["status"] == GalleryStatus.frozen.value:
        raise _frozen()
    return doc


async def _identity_exists(
    db: AsyncDatabase, gallery: ObjectId, identity: ObjectId, *, live: bool = False
) -> bool:
    query = {"_id": identity, "galleryId": gallery}
    if live:
        query |= LIVE
    return await db[IDENTITIES].find_one(query, projection={"_id": 1}) is not None


# ------------------------------------------------------------------ la imagen


def _image_only(form: CreateIdentityForm | AddFaceForm) -> UploadFile:
    sent = [part for part in VECTOR_PARTS if getattr(form, part) is not None]
    if sent:
        raise ErrorReportError(
            400,
            "EMBEDDING_INPUT_NOT_SUPPORTED",
            "Embedding inputs are not supported yet; send an image",
            {"parts": sent},
        )
    if form.image is None:
        raise ErrorReportError(400, "BAD_REQUEST", "An image is required")
    return form.image


async def _face_code(inference: Inference, image: UploadFile, model: str) -> bytes:
    """El código LSSC del rostro de mayor confianza, con el modelo de la galería."""
    data = await image.read()

    def work() -> bytes:
        aligned = best_face(inference.engine, data, "image")
        return lssc.binarize(inference.engine.embed(model, aligned), model)

    return await run(inference, work)


def _face_document(identity: ObjectId, gallery: ObjectId, code: bytes) -> FaceDocument:
    return FaceDocument(
        identityId=identity,
        galleryId=gallery,
        embedding=code,
        isBinarized=True,
        source=FaceSource.image,
    )


# ------------------------------------------------------------------ identidades


async def create_identity(
    db: AsyncDatabase,
    inference: Inference,
    caller: Caller,
    gallery_id: str,
    form: CreateIdentityForm,
) -> Identity:
    """STATE CREATION OPERATION: la identidad con su primer rostro.

    ORDEN DE ESCRITURAS, y lo que deja una caída DESPUÉS de cada paso:

      1. Insertar la identidad, con `faceCount` 0. Aquí salta el `409` de
         `externalId`, sin nada escrito. → Una identidad sin rostro y con
         `faceCount` 0, que la reconciliación encuentra con `{faceCount: 0}`.
      2. Insertar el rostro. → `faceCount` de la identidad corto en 1.
      3. `$inc` del `faceCount` de la identidad. → La galería corta en uno y uno.
      4. `$inc` de `identityCount` y `faceCount` de la galería, en una escritura,
         filtrando la marca de borrado. Si no la encuentra, la galería se está
         borrando o ya no existe: se borran el rostro y la identidad recién
         insertados y la respuesta es `404`.
    """
    image = _image_only(form)
    gallery = await _gallery(db, caller, gallery_id, enrolling=True)
    code = await _face_code(inference, image, gallery["model"])

    doc = IdentityDocument(
        galleryId=gallery["_id"], fullName=form.fullName, externalId=form.externalId
    )
    try:
        doc.id = (await db[IDENTITIES].insert_one(doc.to_document())).inserted_id
    except DuplicateKeyError as exc:
        raise conflict(exc) from None
    face = _face_document(doc.id, gallery["_id"], code)
    face.id = (await db[FACES].insert_one(face.to_document())).inserted_id
    await db[IDENTITIES].update_one({"_id": doc.id}, {"$inc": {"faceCount": 1}})

    counted = await db[GALLERIES].update_one(
        {"_id": gallery["_id"], **LIVE}, {"$inc": {"identityCount": 1, "faceCount": 1}}
    )
    if counted.matched_count == 0:
        await db[FACES].delete_one({"_id": face.id})
        await db[IDENTITIES].delete_one({"_id": doc.id})
        raise _gallery_missing()

    doc.faceCount = 1
    return Identity.from_document(doc)


async def list_identities(
    db: AsyncDatabase, caller: Caller, gallery_id: str, page: int, page_size: int
) -> IdentityPage:
    """RETRIEVAL OPERATION, paginada: el patrón de `list_galleries`.

    `_id` ascendente y `total` en paralelo. El índice `{galleryId}` sirve el
    filtro pero no el orden, y el plan ordena en memoria las identidades de la
    galería: TFM-MIS-MEM/docs#123.
    """
    gallery = await _gallery(db, caller, gallery_id)
    query = {"galleryId": gallery["_id"]}
    skip = (page - 1) * page_size
    count = db[IDENTITIES].count_documents(query)
    if skip > MAX_SKIP:
        docs, total = [], await count
    else:
        cursor = db[IDENTITIES].find(query).sort("_id", ASCENDING).skip(skip).limit(page_size)
        docs, total = await asyncio.gather(cursor.to_list(), count)
    return IdentityPage(
        items=[Identity.from_document(IdentityDocument.model_validate(d)) for d in docs],
        page=page,
        pageSize=page_size,
        total=total,
    )


async def get_identity(
    db: AsyncDatabase, caller: Caller, gallery_id: str, identity_id: str
) -> Identity:
    """RETRIEVAL OPERATION."""
    gallery = await _gallery(db, caller, gallery_id)
    oid = object_id(identity_id, _identity_missing)
    doc = await db[IDENTITIES].find_one({"_id": oid, "galleryId": gallery["_id"]})
    if doc is None:
        raise _identity_missing()
    return Identity.from_document(IdentityDocument.model_validate(doc))


async def update_identity(
    db: AsyncDatabase, caller: Caller, gallery_id: str, identity_id: str, body: IdentityUpdate
) -> Identity:
    """STATE TRANSITION OPERATION sobre `fullName` y `externalId`.

    Las reglas de `update_gallery`: un cuerpo vacío no escribe nada; el `409` lo
    da el índice; poner su propio `externalId` no es un conflicto; permitida
    sobre una galería congelada.
    """
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    if not changes:
        return await get_identity(db, caller, gallery_id, identity_id)

    gallery = await _gallery(db, caller, gallery_id)
    oid = object_id(identity_id, _identity_missing)
    changes["updatedAt"] = utc_now()
    try:
        doc = await db[IDENTITIES].find_one_and_update(
            {"_id": oid, "galleryId": gallery["_id"]},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise conflict(exc) from None
    if doc is None:
        raise _identity_missing()
    return Identity.from_document(IdentityDocument.model_validate(doc))


async def delete_identity(
    db: AsyncDatabase, caller: Caller, gallery_id: str, identity_id: str
) -> None:
    """STATE TRANSITION OPERATION, en cascada sobre sus rostros.

    ORDEN DE ESCRITURAS, y lo que deja una caída DESPUÉS de cada paso:

      1. Marcar la identidad (`deletingAt`). Si no casa, `404` sin nada escrito.
         → La identidad visible; un `addFace` sobre ella da `404`; repetir el
         `DELETE` termina.
      2. Borrar sus rostros. → La identidad visible sin rostros, y la galería
         por encima en los rostros borrados; repetir termina.
      3. Descontar de la galería los rostros borrados. → Lo mismo, sin el
         desfase de rostros.
      4. Borrar la identidad. → La galería por encima en una identidad.
      5. Descontarla de la galería.

    En el orden inverso, una caída dejaría rostros de una identidad que ya no
    existe, y ninguna operación los alcanzaría. Lo prueba
    `tests/contract/test_delete_identity.py`, con caídas inyectadas en 3 y 4.

    La marca cierra la carrera con `addFace`, igual que la de `delete_gallery`:
    un rostro insertado tras el paso 2 no se cuenta en la identidad marcada y
    se deshace.
    """
    gallery = await _gallery(db, caller, gallery_id)
    oid = object_id(identity_id, _identity_missing)
    marked = await db[IDENTITIES].update_one(
        {"_id": oid, "galleryId": gallery["_id"]}, {"$set": {DELETING: utc_now()}}
    )
    if marked.matched_count == 0:
        raise _identity_missing()

    faces = await db[FACES].delete_many({"identityId": oid})
    if faces.deleted_count:
        await db[GALLERIES].update_one(
            {"_id": gallery["_id"]}, {"$inc": {"faceCount": -faces.deleted_count}}
        )
    gone = await db[IDENTITIES].delete_one({"_id": oid})
    if gone.deleted_count:
        await db[GALLERIES].update_one({"_id": gallery["_id"]}, {"$inc": {"identityCount": -1}})


# ------------------------------------------------------------------ rostros


async def add_face(
    db: AsyncDatabase,
    inference: Inference,
    caller: Caller,
    gallery_id: str,
    identity_id: str,
    form: AddFaceForm,
) -> Face:
    """STATE CREATION OPERATION. Sin límite de rostros por identidad.

    ORDEN DE ESCRITURAS, y lo que deja una caída DESPUÉS de cada paso:

      1. Insertar el rostro. → Los dos contadores cortos en 1.
      2. `$inc` del `faceCount` de la identidad, filtrando su marca de borrado.
         Si no la encuentra, se borra el rostro y `404`. → La galería corta.
      3. `$inc` del `faceCount` de la galería, filtrando la suya. Si no la
         encuentra, se borra el rostro, se descuenta de la identidad y `404`.
    """
    image = _image_only(form)
    gallery = await _gallery(db, caller, gallery_id, enrolling=True)
    oid = object_id(identity_id, _identity_missing)
    if not await _identity_exists(db, gallery["_id"], oid, live=True):
        raise _identity_missing()
    code = await _face_code(inference, image, gallery["model"])

    face = _face_document(oid, gallery["_id"], code)
    face.id = (await db[FACES].insert_one(face.to_document())).inserted_id

    counted = await db[IDENTITIES].update_one(
        {"_id": oid, "galleryId": gallery["_id"], **LIVE}, {"$inc": {"faceCount": 1}}
    )
    if counted.matched_count == 0:
        await db[FACES].delete_one({"_id": face.id})
        raise _identity_missing()

    counted = await db[GALLERIES].update_one(
        {"_id": gallery["_id"], **LIVE}, {"$inc": {"faceCount": 1}}
    )
    if counted.matched_count == 0:
        await db[FACES].delete_one({"_id": face.id})
        await db[IDENTITIES].update_one({"_id": oid}, {"$inc": {"faceCount": -1}})
        raise _gallery_missing()

    return Face.from_document(face)


async def delete_face(
    db: AsyncDatabase, caller: Caller, gallery_id: str, identity_id: str, face_id: str
) -> None:
    """STATE TRANSITION OPERATION. `409 LAST_FACE_OF_IDENTITY` para el último.

    ORDEN, y lo que deja una caída DESPUÉS de cada paso:

      0. Leer el rostro por `{_id, identityId, galleryId}`. Sin esta lectura, un
         rostro inexistente de una identidad con uno solo daría `409` y no `404`.
      1. Decrementar el `faceCount` de la identidad SOLO SI es mayor que 1. Si
         no casa, `409` (o `404` si la identidad ya no existe). Es el decremento
         condicional, y no una lectura previa, lo que impide que dos borrados
         concurrentes la dejen sin rostros. → `faceCount` corto en 1: como
         mucho, un `409` de más.
      2. Borrar el rostro. Si otro borrado concurrente se lo llevó antes, se
         devuelve el decremento y `404`. → La galería por encima en 1.
      3. Descontarlo de la galería.
    """
    gallery = await _gallery(db, caller, gallery_id)
    oid = object_id(identity_id, _identity_missing)
    fid = object_id(face_id, _face_missing)
    face = {"_id": fid, "identityId": oid, "galleryId": gallery["_id"]}
    if await db[FACES].find_one(face, projection={"_id": 1}) is None:
        if not await _identity_exists(db, gallery["_id"], oid):
            raise _identity_missing()
        raise _face_missing()

    discounted = await db[IDENTITIES].update_one(
        {"_id": oid, "galleryId": gallery["_id"], "faceCount": {"$gt": 1}},
        {"$inc": {"faceCount": -1}},
    )
    if discounted.matched_count == 0:
        if not await _identity_exists(db, gallery["_id"], oid):
            raise _identity_missing()
        raise _last_face()

    gone = await db[FACES].delete_one(face)
    if gone.deleted_count == 0:
        await db[IDENTITIES].update_one({"_id": oid}, {"$inc": {"faceCount": 1}})
        raise _face_missing()
    await db[GALLERIES].update_one({"_id": gallery["_id"]}, {"$inc": {"faceCount": -1}})
