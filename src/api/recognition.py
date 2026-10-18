"""Inferencia con estado: `…/identities/{identityId}/verify` y `…/galleries/{galleryId}/search`.

La identidad se exige en el ROUTER, como en el resto: una ruta que se añada aquí
nace protegida, y `tests/contract/test_identity_guard.py` lo comprueba desde el
contrato.

Los cuerpos multipart se declaran como un modelo cerrado anotado con `File()`,
no con `Form()`: así FastAPI rechaza la parte de más y publica
`multipart/form-data` (src/models/__init__.py).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File
from pymongo.asynchronous.database import AsyncDatabase

from src.api.inference import get_inference
from src.controllers import recognition as controller
from src.controllers.inference import Inference
from src.core.database import get_db
from src.core.identity import Identity as Caller
from src.core.identity import require_identity
from src.models.inference import VerificationResult
from src.models.recognition import SearchGalleryForm, SearchResult, VerifyAgainstIdentityForm

router = APIRouter(prefix="/v1/fr/commercial", dependencies=[Depends(require_identity)])


@router.post(
    "/galleries/{galleryId}/identities/{identityId}/verify",
    response_model=VerificationResult,
    operation_id="verifyAgainstIdentity",
)
async def verify_against_identity(
    galleryId: str,  # noqa: N803 — el nombre del contrato
    identityId: str,  # noqa: N803
    form: Annotated[VerifyAgainstIdentityForm, File()],
    caller: Caller = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
    inference: Inference = Depends(get_inference),
) -> VerificationResult:
    return await controller.verify_against_identity(
        db, inference, caller, galleryId, identityId, form
    )


@router.post(
    "/galleries/{galleryId}/search", response_model=SearchResult, operation_id="searchGallery"
)
async def search_gallery(
    galleryId: str,  # noqa: N803
    form: Annotated[SearchGalleryForm, File()],
    caller: Caller = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
    inference: Inference = Depends(get_inference),
) -> SearchResult:
    return await controller.search_gallery(db, inference, caller, galleryId, form)
