"""Operaciones de galerías: `/v1/fr/commercial/galleries`.

La identidad se exige en el ROUTER, no operación por operación: una ruta que se
añada aquí nace protegida. Hay además una prueba que, desde el contrato, exige
`401` sin cabeceras a toda operación implementada (`tests/contract/
test_identity_guard.py`).
"""

from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase

from src.controllers import galleries as controller
from src.core.database import get_db
from src.core.identity import Identity, require_identity
from src.models.gallery import Gallery

router = APIRouter(prefix="/v1/fr/commercial", dependencies=[Depends(require_identity)])


@router.get("/galleries/{galleryId}", response_model=Gallery, operation_id="getGallery")
async def get_gallery(
    galleryId: str,  # noqa: N803 — el nombre del contrato
    identity: Identity = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
) -> Gallery:
    return await controller.get_gallery(db, identity, galleryId)
