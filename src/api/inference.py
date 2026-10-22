"""Inferencia sin estado: `/detection`, `/verification` y `/extract-embeddings`.

La identidad se exige en el ROUTER, como en galerías: una ruta que se añada aquí
nace protegida, y `tests/contract/test_identity_guard.py` lo comprueba desde el
contrato.

El cuerpo se declara como un modelo cerrado anotado con `File()`, no con
`Form()`: así FastAPI rechaza la parte de más y publica `multipart/form-data`
(src/models/__init__.py).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Request

from src.controllers import inference as controller
from src.controllers.inference import Inference
from src.core.identity import Identity, require_identity
from src.core.plan import Plan, require_plan
from src.models.inference import (
    DetectFacesForm,
    DetectFacesResult,
    ExtractEmbeddingForm,
    ExtractEmbeddingResult,
    VerificationResult,
    VerifyPairForm,
)

router = APIRouter(
    prefix="/v1/fr/commercial",
    dependencies=[Depends(require_identity), Depends(require_plan)],
)


def get_inference(request: Request) -> Inference:
    return request.app.state.inference


@router.post("/detection", response_model=DetectFacesResult, operation_id="detectFaces")
async def detect_faces(
    form: Annotated[DetectFacesForm, File()],
    plan: Plan = Depends(require_plan),
    inference: Inference = Depends(get_inference),
) -> DetectFacesResult:
    return await controller.detect_faces(inference, plan, form)


@router.post("/verification", response_model=VerificationResult, operation_id="verifyPair")
async def verify_pair(
    form: Annotated[VerifyPairForm, File()],
    plan: Plan = Depends(require_plan),
    inference: Inference = Depends(get_inference),
) -> VerificationResult:
    return await controller.verify_pair(inference, plan, form)


@router.post(
    "/extract-embeddings",
    response_model=ExtractEmbeddingResult,
    operation_id="extractEmbedding",
)
async def extract_embedding(
    form: Annotated[ExtractEmbeddingForm, File()],
    identity: Identity = Depends(require_identity),
    plan: Plan = Depends(require_plan),
    inference: Inference = Depends(get_inference),
) -> ExtractEmbeddingResult:
    return await controller.extract_embedding(inference, identity, plan, form)
