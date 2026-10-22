"""Identidades y rostros: `/v1/fr/commercial/galleries/{galleryId}/identities`.

La identidad se exige en el ROUTER, como en galerías e inferencia: una ruta que
se añada aquí nace protegida, y `tests/contract/test_identity_guard.py` lo
comprueba desde el contrato.

Los cuerpos multipart se declaran como un modelo cerrado anotado con `File()`,
no con `Form()`: así FastAPI rechaza la parte de más y publica
`multipart/form-data` (src/models/__init__.py).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response
from pymongo.asynchronous.database import AsyncDatabase

from src.api.inference import get_inference
from src.controllers import identities as controller
from src.controllers.inference import Inference
from src.core.database import get_db
from src.core.identity import Identity as Caller
from src.core.identity import require_identity
from src.core.plan import Plan, require_plan, requires
from src.models.face import AddFaceForm, Face
from src.models.identity import CreateIdentityForm, Identity, IdentityPage, IdentityUpdate

router = APIRouter(
    prefix="/v1/fr/commercial",
    dependencies=[
        Depends(require_identity),
        Depends(require_plan),
        Depends(requires("identityManagement")),
    ],
)

IDENTITIES = "/galleries/{galleryId}/identities"
IDENTITY = IDENTITIES + "/{identityId}"


@router.post(IDENTITIES, response_model=Identity, status_code=201, operation_id="createIdentity")
async def create_identity(
    galleryId: str,  # noqa: N803 — el nombre del contrato
    form: Annotated[CreateIdentityForm, File()],
    caller: Caller = Depends(require_identity),
    plan: Plan = Depends(require_plan),
    db: AsyncDatabase = Depends(get_db),
    inference: Inference = Depends(get_inference),
) -> Identity:
    return await controller.create_identity(db, inference, caller, plan, galleryId, form)


# Las cotas, las del contrato: fuera de rango es `400`, nunca se acota en
# silencio (`components.parameters.Page` y `PageSize`).
@router.get(IDENTITIES, response_model=IdentityPage, operation_id="listIdentities")
async def list_identities(
    galleryId: str,  # noqa: N803
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),  # noqa: N803
    caller: Caller = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
) -> IdentityPage:
    return await controller.list_identities(db, caller, galleryId, page, pageSize)


@router.get(IDENTITY, response_model=Identity, operation_id="getIdentity")
async def get_identity(
    galleryId: str,  # noqa: N803
    identityId: str,  # noqa: N803
    caller: Caller = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
) -> Identity:
    return await controller.get_identity(db, caller, galleryId, identityId)


@router.patch(IDENTITY, response_model=Identity, operation_id="updateIdentity")
async def update_identity(
    galleryId: str,  # noqa: N803
    identityId: str,  # noqa: N803
    body: IdentityUpdate,
    caller: Caller = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
) -> Identity:
    return await controller.update_identity(db, caller, galleryId, identityId, body)


@router.delete(IDENTITY, status_code=204, response_class=Response, operation_id="deleteIdentity")
async def delete_identity(
    galleryId: str,  # noqa: N803
    identityId: str,  # noqa: N803
    caller: Caller = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
) -> Response:
    await controller.delete_identity(db, caller, galleryId, identityId)
    return Response(status_code=204)


@router.post(IDENTITY + "/faces", response_model=Face, status_code=201, operation_id="addFace")
async def add_face(
    galleryId: str,  # noqa: N803
    identityId: str,  # noqa: N803
    form: Annotated[AddFaceForm, File()],
    caller: Caller = Depends(require_identity),
    plan: Plan = Depends(require_plan),
    db: AsyncDatabase = Depends(get_db),
    inference: Inference = Depends(get_inference),
) -> Face:
    return await controller.add_face(db, inference, caller, plan, galleryId, identityId, form)


@router.delete(
    IDENTITY + "/faces/{faceId}",
    status_code=204,
    response_class=Response,
    operation_id="deleteFace",
)
async def delete_face(
    galleryId: str,  # noqa: N803
    identityId: str,  # noqa: N803
    faceId: str,  # noqa: N803
    caller: Caller = Depends(require_identity),
    db: AsyncDatabase = Depends(get_db),
) -> Response:
    await controller.delete_face(db, caller, galleryId, identityId, faceId)
    return Response(status_code=204)
