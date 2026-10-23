"""Operaciones de galerías: `/v1/fr/commercial/galleries`.

La identidad se exige en el ROUTER, no operación por operación: una ruta que se
añada aquí nace protegida. Hay además una prueba que, desde el contrato, exige
`401` sin cabeceras a toda operación implementada (`tests/contract/
test_identity_guard.py`).
"""

from fastapi import APIRouter, Depends, Query, Response
from pymongo.asynchronous.database import AsyncDatabase

from src.controllers import galleries as controller
from src.core.database import get_db
from src.core.identity import Identity, require_identity
from src.core.plan import Plan, require_plan, requires
from src.models.gallery import Gallery, GalleryCreate, GalleryPage, GalleryUpdate

router = APIRouter(
    prefix="/v1/fr/commercial",
    dependencies=[
        Depends(require_identity),
        Depends(require_plan),
        Depends(requires("galleryManagement")),
    ],
)


@router.post("/galleries", response_model=Gallery, status_code=201, operation_id="createGallery")
async def create_gallery(
    body: GalleryCreate,
    identity: Identity = Depends(require_identity),
    plan: Plan = Depends(require_plan),
    db: AsyncDatabase = Depends(get_db),
) -> Gallery:
    return await controller.create_gallery(db, identity, plan, body)


# Las cotas, las del contrato: fuera de rango es `400`, nunca se acota en
# silencio (`components.parameters.Page` y `PageSize`).
@router.get("/galleries", response_model=GalleryPage, operation_id="listGalleries")
async def list_galleries(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),  # noqa: N803 — el nombre del contrato
    identity: Identity = Depends(require_identity),
    plan: Plan = Depends(require_plan),
    db: AsyncDatabase = Depends(get_db),
) -> GalleryPage:
    return await controller.list_galleries(db, identity, plan, page, pageSize)


@router.get("/galleries/{galleryId}", response_model=Gallery, operation_id="getGallery")
async def get_gallery(
    galleryId: str,  # noqa: N803 — el nombre del contrato
    identity: Identity = Depends(require_identity),
    plan: Plan = Depends(require_plan),
    db: AsyncDatabase = Depends(get_db),
) -> Gallery:
    return await controller.get_gallery(db, identity, plan, galleryId)


@router.patch("/galleries/{galleryId}", response_model=Gallery, operation_id="updateGallery")
async def update_gallery(
    galleryId: str,  # noqa: N803 — el nombre del contrato
    body: GalleryUpdate,
    identity: Identity = Depends(require_identity),
    plan: Plan = Depends(require_plan),
    db: AsyncDatabase = Depends(get_db),
) -> Gallery:
    return await controller.update_gallery(db, identity, plan, galleryId, body)


@router.delete(
    "/galleries/{galleryId}",
    status_code=204,
    response_class=Response,
    operation_id="deleteGallery",
)
async def delete_gallery(
    galleryId: str,  # noqa: N803 — el nombre del contrato
    identity: Identity = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
) -> Response:
    await controller.delete_gallery(db, identity, galleryId)
    return Response(status_code=204)
