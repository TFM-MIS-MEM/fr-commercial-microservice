"""Inferencia sin estado: `detectFaces`, `verifyPair` y `extractEmbedding`.

Las tres son COMPUTATION FUNCTION: no leen ni escriben estado del servidor.

TODO EL TRABAJO DE CPU VA A UN HILO Y BAJO UN SEMÁFORO. Decodificar, detectar y
extraer bloquean; en el bucle de eventos congelarían todas las peticiones,
también `/health`. El semáforo (`INFERENCE_CONCURRENCY`, 1 por omisión) acota
cuántas inferencias corren a la vez: cada una usa ya los hilos de torch
(`TORCH_NUM_THREADS`), y apilarlas sin tope solo reparte la misma CPU y la misma
memoria entre más peticiones. Los valores de la campaña se fijan en la sesión
del instrumento.

LOS HILOS DE TORCH SE FIJAN EN CADA HILO DE TRABAJO, no una vez al arrancar.
Con el motor OpenMP de torch, `set_num_threads` es estado DEL HILO que lo llama:
medido en el contenedor, un hilo del pool creado antes del ajuste sigue con los
10 hilos por omisión aunque el principal ya diga 2. La inferencia corre en los
hilos del pool, así que el ajuste se aplica ahí, antes de cada trabajo; cuesta
una comparación cuando ya está puesto.

EL REGISTRO DE TIEMPOS. Cada inferencia escribe, en `debug`, cuánto tardaron
la detección y la extracción del vector. Desde F10 la decodificación va
aparte, antes de imputar unidades (`read_image`), y no entra en esos tiempos. Es el desglose de
las latencias del PR de #37 y el que necesitará la campaña de medición; no sale
en la respuesta, que el contrato no lo declara.

EL PLAN (F10). Cada operación comprueba su funcionalidad, el modelo y el
umbral; decodifica la imagen (`400` sin cobrar); imputa sus unidades
(`403 QUOTA_EXCEEDED`); y solo entonces infiere. Lo que falle a partir de ahí
---`NO_FACE_DETECTED`, por ejemplo--- queda cobrado.

QUÉ ROSTRO. Con varios, el de mayor confianza (src/inference/engine.py). Sin
ninguno, `detectFaces` responde `200` con la lista vacía y las otras dos `400`
`NO_FACE_DETECTED`: no tienen vector que devolver.
"""

import asyncio
import base64
import io
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import TypeVar

import numpy as np
import torch
from fastapi import UploadFile
from PIL import Image
from starlette.concurrency import run_in_threadpool

from src.core.errors import ErrorReportError
from src.core.identity import Identity
from src.core.images import open_image
from src.core.logger import get_logger
from src.core.plan import UNITS, Plan
from src.inference import lssc, provenance
from src.inference.engine import Engine, versioned
from src.models.inference import (
    BoundingBox,
    DetectedFace,
    DetectFacesForm,
    DetectFacesResult,
    ExtractEmbeddingForm,
    ExtractEmbeddingResult,
    VerificationResult,
    VerifyPairForm,
)

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class Inference:
    """Lo que el `lifespan` deja en `app.state.inference`."""

    engine: Engine
    semaphore: asyncio.Semaphore
    token_secret: str
    torch_threads: int | None = None


async def run(inference: Inference, work: Callable[[], T]) -> T:
    def in_worker() -> T:
        wanted = inference.torch_threads
        if wanted is not None and torch.get_num_threads() != wanted:
            torch.set_num_threads(wanted)
        return work()

    async with inference.semaphore:
        return await run_in_threadpool(in_worker)


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 2)


def _png_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def read_image(inference: Inference, upload: UploadFile) -> Image.Image:
    """La imagen decodificada, o `400 INVALID_IMAGE` / `IMAGE_TOO_LARGE`.

    DECODIFICAR VA APARTE, ANTES DE IMPUTAR UNIDADES (F10): una imagen que no se
    puede leer se rechaza sin cobrar. Bajo el mismo semáforo que la inferencia,
    para que el trabajo de CPU siga acotado por `INFERENCE_CONCURRENCY`; el
    precio, con el servicio saturado, es esperar dos veces a la cola.
    """
    data = await upload.read()
    return await run(inference, lambda: open_image(data))


def best_face(engine: Engine, image: Image.Image, part: str) -> Image.Image:
    """El recorte alineado del rostro de mayor confianza, o `NO_FACE_DETECTED`."""
    faces = engine.detect(image)
    if not faces:
        raise ErrorReportError(
            400, "NO_FACE_DETECTED", "No face was detected in the image", {"part": part}
        )
    return engine.align(image, faces[0])


async def detect_faces(
    inference: Inference, plan: Plan, form: DetectFacesForm
) -> DetectFacesResult:
    plan.require("faceDetection")
    image = await read_image(inference, form.image)
    await plan.consume("faceDetection", UNITS["faceDetection"])

    def work() -> DetectFacesResult:
        started = perf_counter()
        faces = inference.engine.detect(image)
        if form.onlyFirst:
            faces = faces[:1]
        detected = perf_counter()
        result = DetectFacesResult(
            faces=[
                DetectedFace(
                    boundingBox=BoundingBox(x=f.x, y=f.y, width=f.width, height=f.height),
                    confidence=f.confidence,
                    crop=(
                        _png_base64(inference.engine.align(image, f)) if form.includeCrops else None
                    ),
                )
                for f in faces
            ],
            numFaces=len(faces),
        )
        logger.debug(
            "inference_timing",
            operation="detectFaces",
            torch_threads=torch.get_num_threads(),
            pixels=image.width * image.height,
            detect_ms=_ms(detected - started),
            crops_ms=_ms(perf_counter() - detected),
        )
        return result

    return await run(inference, work)


VECTOR_PARTS = ("embedding1", "embeddingToken1", "embedding2", "embeddingToken2")


async def verify_pair(inference: Inference, plan: Plan, form: VerifyPairForm) -> VerificationResult:
    sent = [part for part in VECTOR_PARTS if getattr(form, part) is not None]
    if sent:
        raise ErrorReportError(
            400,
            "EMBEDDING_INPUT_NOT_SUPPORTED",
            "Embedding inputs are not supported yet; send image1 and image2",
            {"parts": sent},
        )
    if form.image1 is None or form.image2 is None:
        raise ErrorReportError(400, "BAD_REQUEST", "image1 and image2 are required")

    model = form.model.value
    plan.require("pairVerification")
    plan.require_model(model)
    plan.require_threshold(form.threshold)
    image1 = await read_image(inference, form.image1)
    image2 = await read_image(inference, form.image2)
    await plan.consume("pairVerification", UNITS["pairVerification"])

    def work() -> VerificationResult:
        started = perf_counter()
        aligned1 = best_face(inference.engine, image1, "image1")
        aligned2 = best_face(inference.engine, image2, "image2")
        detected = perf_counter()
        code1 = lssc.binarize(inference.engine.embed(model, aligned1), model)
        code2 = lssc.binarize(inference.engine.embed(model, aligned2), model)
        embedded = perf_counter()

        confidence = lssc.confidence(lssc.hamming(code1, code2))
        threshold = form.threshold if form.threshold is not None else lssc.default_threshold(model)
        logger.debug(
            "inference_timing",
            operation="verifyPair",
            torch_threads=torch.get_num_threads(),
            model=model,
            detect_ms=_ms(detected - started),
            embed_ms=_ms(embedded - detected),
        )
        return VerificationResult(
            isTheSame=confidence >= threshold,
            confidence=confidence,
            thresholdUsed=threshold,
            model=versioned(model),
        )

    return await run(inference, work)


async def extract_embedding(
    inference: Inference, identity: Identity, plan: Plan, form: ExtractEmbeddingForm
) -> ExtractEmbeddingResult:
    if identity.channel != "api":
        # `extractEmbedding` declara solo `apiKeyAuth`: un JWT no es una
        # credencial admitida para ella, y por eso `401` y no `403`.
        raise ErrorReportError(
            401, "API_CHANNEL_REQUIRED", "This operation is only available on the API channel"
        )

    model = form.model.value
    plan.require("embeddingExtraction")
    plan.require_model(model)
    image = await read_image(inference, form.image)
    await plan.consume("embeddingExtraction", UNITS["embeddingExtraction"])

    def work() -> ExtractEmbeddingResult:
        started = perf_counter()
        aligned = best_face(inference.engine, image, "image")
        detected = perf_counter()
        vector = inference.engine.embed(model, aligned)
        if form.returnBinarized:
            raw = lssc.binarize(vector, model)
        else:
            raw = vector.astype(np.dtype("<f4")).tobytes()
        embedded = perf_counter()
        logger.debug(
            "inference_timing",
            operation="extractEmbedding",
            torch_threads=torch.get_num_threads(),
            model=model,
            detect_ms=_ms(detected - started),
            embed_ms=_ms(embedded - detected),
        )
        return ExtractEmbeddingResult(
            embedding=base64.b64encode(raw).decode("ascii"),
            embeddingToken=provenance.sign(
                inference.token_secret, versioned(model), form.returnBinarized, raw
            ),
            model=versioned(model),
            isBinarized=form.returnBinarized,
        )

    return await run(inference, work)
