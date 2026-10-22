"""Inferencia con estado: `verifyAgainstIdentity` y `searchGallery` (F8, #40).

Las dos son RETRIEVAL OPERATION: no escriben nada, y su resultado depende de los
rostros guardados.

ORDEN, el de las altas de F4 (src/controllers/identities.py), y la inferencia
después de todo lo que no la necesita:

  1. Lo que no necesita estado: `livenessToken` (`400 LIVENESS_NOT_SUPPORTED`
     hasta F12: aceptarlo sin comprobarlo sería dar por superada una prueba que
     nadie ha hecho), las entradas por vector (`400
     EMBEDDING_INPUT_NOT_SUPPORTED` hasta F7) y la imagen que falta (`400`).
  2. El plan: `threshold` exige el umbral configurable (`403
     FEATURE_NOT_IN_PLAN`); la funcionalidad la comprueba la ruta.
  3. La cadena (`404`, también con marca de borrado o identificador imposible,
     también para `ADMIN`) y la congelación (`403
     GALLERY_FROZEN_MODEL_NOT_IN_PLAN`, guardada o porque el plan no incluye el
     modelo de la galería), ANTES de inferir.
  4. La imagen decodificada y las unidades imputadas (`403 QUOTA_EXCEEDED`):
     1,5 la verificación; la búsqueda, 1,5 + 0,05 por cada 1.000 rostros de la
     galería, proporcional, con su `faceCount`. Lo que falle después queda
     cobrado.
  5. La inferencia, por el camino de F4 (`identities.lssc_code`): rostro de
     mayor confianza con el modelo DE LA GALERÍA, y su código LSSC.
     `INVALID_IMAGE`, `IMAGE_TOO_LARGE` y `NO_FACE_DETECTED` como en F5.
  6. La comparación, por distancia de Hamming contra los códigos guardados.

LA DECISIÓN se toma con `lssc.confidence(int(d))`, la MISMA expresión que
`lssc.default_threshold`, nunca sobre un vector de confianzas de numpy: así una
distancia igual a `d_max` da exactamente el umbral y cuenta como coincidencia,
como en `verifyPair`.

Sin `threshold`, el del modelo de la galería.

HASTA F11: `verifyAgainstIdentity` declara `enrollmentToken`, pero la
dependencia de identidad sigue exigiendo las tres cabeceras de la pasarela.
`searchGallery` no lo declara ni lo admitirá: devolvería el nombre de la
identidad a quien solo tiene un token.

DÓNDE SE COMPARA: en el hilo de trabajo y bajo el semáforo de la inferencia
(`inference.run`), nunca en el bucle de eventos. En el bucle, a 50.000 rostros,
cada comparación lo congelaría unos milisegundos, y retrasaría las consultas de
las demás búsquedas y `/health`: la parte de «recuperación» de la medida
incluiría tiempo que no es suyo. Bajo el semáforo, el trabajo de CPU sigue
acotado por `INFERENCE_CONCURRENCY`, y la configuración de la campaña conserva
su significado. El precio: con el servicio saturado, una búsqueda hace cola dos
veces, una para inferir y otra para comparar, y eso sale en el p95.

  - `verifyAgainstIdentity` lee sus k rostros ANTES de inferir y compara en el
    mismo trabajo: una sola cola, y la identidad sin rostros sale sin gastar
    inferencia.
  - `searchGallery` no puede hacer lo mismo: 50.000 rostros esperando en la cola
    del semáforo son ~10 MB por petición encolada, y una imagen sin rostro
    gastaría la consulta. Infiere, recupera y compara, en ese orden.

EL REGISTRO DE TIEMPOS, en `debug` como el de F5: `faces`, `retrieve_ms` (desde
el `find` hasta tener los códigos: ejecución en MongoDB, transferencia y
decodificación BSON, que en pymongo asíncrono ocurre en el bucle) y
`compare_ms`, medido dentro del hilo, sin la espera del semáforo.
"""

from time import perf_counter

import numpy as np
import torch
from bson import ObjectId
from bson.raw_bson import RawBSONDocument
from pymongo.asynchronous.database import AsyncDatabase

from src.controllers.common import not_found, object_id
from src.controllers.identities import (
    face_code,
    identity_exists,
    image_only,
    lssc_code,
    owned_gallery,
)
from src.controllers.inference import Inference, read_image, run
from src.core.errors import ErrorReportError
from src.core.identity import Identity as Caller
from src.core.logger import get_logger
from src.core.plan import UNITS, Plan, search_units
from src.documents.base import LIVE
from src.documents.face import COLLECTION as FACES
from src.documents.identity import COLLECTION as IDENTITIES
from src.documents.identity import IdentityDocument
from src.inference import lssc
from src.inference.engine import versioned
from src.models.identity import Identity
from src.models.inference import VerificationResult
from src.models.recognition import SearchGalleryForm, SearchResult, VerifyAgainstIdentityForm

logger = get_logger(__name__)


def _identity_missing() -> ErrorReportError:
    return not_found("Identity")


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 2)


def _no_liveness(form: VerifyAgainstIdentityForm) -> None:
    if form.livenessToken is not None:
        raise ErrorReportError(
            400,
            "LIVENESS_NOT_SUPPORTED",
            "Liveness is not supported yet; send the request without livenessToken",
            {"parts": ["livenessToken"]},
        )


#: El elemento BSON del código: tipo binario (0x05) y nombre; después, su
#: longitud (int32, little-endian) y el subtipo genérico (0x00).
_EMBEDDING = b"\x05embedding\x00"
_CODE_HEADER = lssc.CODE_BYTES.to_bytes(4, "little") + b"\x00"


def stored_code(face: RawBSONDocument) -> bytes:
    """El código guardado de un rostro, cortado de su BSON sin decodificarlo.

    POR QUÉ. Medido en F8 a 50.000 rostros: la recuperación tardaba 101 ms de
    mediana frente a 22 ms de ejecución en MongoDB. El resto es decodificar en
    Python, y la decodificación por omisión crea por documento un `dict`, sus
    claves y dos `ObjectId`. Con un worker eso va en serie, y moverlo a un hilo
    no lo reduce: no suelta el GIL. Lo que se reduce es el trabajo por
    documento: pymongo parte el lote en `RawBSONDocument` sin decodificar sus
    campos, y aquí se busca el elemento y se corta el código.

    POR QUÉ EL PRIMER ACIERTO ES EL ELEMENTO. Con la proyección de
    `search_gallery`, lo único que puede precederlo son `_id` e `identityId`:
    dos `ObjectId` de 12 bytes, que no pueden contener el nombre y la cabecera
    (16 bytes), y el nombre no puede empezar dentro de uno y terminar en el
    elemento siguiente (`0x05` solo aparece al principio del patrón). Si algo
    no casa ---otra longitud, otro subtipo---, se decodifica el documento.
    """
    raw = face.raw
    at = raw.find(_EMBEDDING, 4) + len(_EMBEDDING)
    if at >= len(_EMBEDDING) and raw[at : at + 5] == _CODE_HEADER:
        return raw[at + 5 : at + 5 + lssc.CODE_BYTES]
    return face["embedding"]


def _threshold(requested: float | None, model: str) -> float:
    return requested if requested is not None else lssc.default_threshold(model)


async def verify_against_identity(
    db: AsyncDatabase,
    inference: Inference,
    caller: Caller,
    plan: Plan,
    gallery_id: str,
    identity_id: str,
    form: VerifyAgainstIdentityForm,
) -> VerificationResult:
    """1:1 contra los rostros de la identidad: cuenta la MENOR distancia.

    Cada rostro añadido es una oportunidad más de coincidir: la tasa de falsa
    aceptación crece con el número de rostros de la identidad.

    UNA IDENTIDAD SIN ROSTROS ES `404`: solo puede ser el residuo de un
    `createIdentity` que falló después de crearla, cuyo cliente recibió un
    error; para él no existe. Que `getIdentity` la muestre es cosa de la
    reconciliación.
    """
    _no_liveness(form)
    image = image_only(form)
    plan.require_threshold(form.threshold)
    gallery = await owned_gallery(db, caller, gallery_id, usable=plan)
    oid = object_id(identity_id, _identity_missing)
    if not await identity_exists(db, gallery["_id"], oid, live=True):
        raise _identity_missing()
    faces = db[FACES].find(
        {"identityId": oid, "galleryId": gallery["_id"]}, projection={"embedding": 1, "_id": 0}
    )
    codes = [face["embedding"] for face in await faces.to_list()]
    if not codes:
        raise _identity_missing()

    model = gallery["model"]
    picture = await read_image(inference, image)
    await plan.consume("identityVerification", UNITS["identityVerification"])

    def work() -> int:
        started = perf_counter()
        probe = lssc_code(inference.engine, picture, model)
        inferred = perf_counter()
        d_min = int(lssc.hamming_many(probe, codes).min())
        logger.debug(
            "inference_timing",
            operation="verifyAgainstIdentity",
            torch_threads=torch.get_num_threads(),
            model=model,
            faces=len(codes),
            infer_ms=_ms(inferred - started),
            compare_ms=_ms(perf_counter() - inferred),
        )
        return d_min

    d_min = await run(inference, work)
    confidence = lssc.confidence(d_min)
    threshold = _threshold(form.threshold, model)
    return VerificationResult(
        isTheSame=confidence >= threshold,
        confidence=confidence,
        thresholdUsed=threshold,
        model=versioned(model),
    )


async def search_gallery(
    db: AsyncDatabase,
    inference: Inference,
    caller: Caller,
    plan: Plan,
    gallery_id: str,
    form: SearchGalleryForm,
) -> SearchResult:
    """1:N contra todos los rostros de la galería: gana el más cercano.

    LA RECUPERACIÓN ES UNA SOLA CONSULTA sobre `faces` por `galleryId`,
    proyectada a `embedding` e `identityId` (y el `_id`, para los empates),
    servida por el índice `{galleryId}`. Sin caché: `f(n_galería)` en la fórmula
    de unidades es precisamente esta recuperación, y es lo que mide el perfil
    1:N. Los documentos llegan como `RawBSONDocument` y el código se corta de
    sus bytes (`stored_code`); `_id` e `identityId` solo se decodifican del
    ganador y de los empatados con él.

    EMPATES: a igual distancia, el de menor identificador (`_id` del rostro),
    para que el resultado sea determinista. No es «el enrolado antes»: un
    `ObjectId` solo sigue el orden de alta dentro de un mismo proceso, y con
    varios workers en el mismo segundo no. Sin `sort` en MongoDB, que sería
    ordenar en memoria todos los rostros de la galería: el desempate se hace
    entre los de distancia mínima, que casi siempre es uno.

    SIN COINCIDENCIA, `identity` es `null` y `confidence` no se emite: sin
    identidad, no hay de quién sea esa confianza.

    EL MEJOR ROSTRO DE UNA IDENTIDAD MARCADA O YA BORRADA es «sin coincidencia»,
    sin pasar al siguiente candidato. Esos rostros son residuo de un borrado en
    curso, o caído y pendiente de repetir. Durante esa ventana, responder sin
    coincidencia es conservador: nunca devuelve una identidad que no sea la del
    mejor rostro. Se paga con un posible falso negativo en la ventana, y se
    acepta para no ordenar candidatos ni añadir un `$in`.
    """
    image = image_only(form)
    plan.require_threshold(form.threshold)
    gallery = await owned_gallery(db, caller, gallery_id, usable=plan)
    model = gallery["model"]
    picture = await read_image(inference, image)
    await plan.consume("gallerySearch", search_units(gallery.get("faceCount", 0)))
    probe = await face_code(inference, picture, model)

    started = perf_counter()
    collection = db[FACES]
    raw = collection.with_options(
        codec_options=collection.codec_options.with_options(document_class=RawBSONDocument)
    )
    faces = raw.find({"galleryId": gallery["_id"]}, projection={"embedding": 1, "identityId": 1})
    stored = await faces.to_list()
    codes = [stored_code(face) for face in stored]
    retrieved = perf_counter()
    if not codes:
        return SearchResult(identity=None)

    def work() -> tuple[int, int]:
        compared = perf_counter()
        distances = lssc.hamming_many(probe, codes)
        d_min = distances.min()
        tied = np.flatnonzero(distances == d_min)
        best = int(tied[0]) if len(tied) == 1 else min(tied, key=lambda i: stored[i]["_id"])
        logger.debug(
            "inference_timing",
            operation="searchGallery",
            torch_threads=torch.get_num_threads(),
            model=model,
            faces=len(codes),
            retrieve_ms=_ms(retrieved - started),
            compare_ms=_ms(perf_counter() - compared),
        )
        return int(best), int(d_min)

    best, d_min = await run(inference, work)
    confidence = lssc.confidence(d_min)
    if confidence < _threshold(form.threshold, model):
        return SearchResult(identity=None)

    identity: ObjectId = stored[best]["identityId"]
    doc = await db[IDENTITIES].find_one({"_id": identity, "galleryId": gallery["_id"], **LIVE})
    if doc is None:
        return SearchResult(identity=None)
    return SearchResult(
        identity=Identity.from_document(IdentityDocument.model_validate(doc)),
        confidence=confidence,
    )
