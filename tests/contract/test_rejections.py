"""Rechazos que el contrato declara como `400`, con el manejador de errores real.

Ninguna de estas operaciones está implementada (el guardián de montaje sigue
exigiendo exactamente `{getGallery}`), así que se sirven sobre una aplicación
mínima en las rutas CANÓNICAS, con las cotas copiadas del contrato y con
`install_error_handlers`, que es lo que convierte la validación en `400`
`ErrorReport`. Es el patrón que heredan los tres listados cuando se
implementen: cada cota del contrato, una petición fuera de rango.

Antes de docs#103 ningún listado declaraba `400`, y para `pageSize=101` no
existía implementación conforme: aceptar incumplía el `maximum` y rechazar
emitía un estado no declarado (medido en #33). Estas pruebas lo afirman en los
dos sentidos: fuera de rango, `400` conforme; dentro, `200` conforme.

ALCANCE: el campo de más se prueba aquí en un cuerpo JSON. Los 8 cuerpos
`multipart/form-data` quedan fuera: `python-multipart` no está en el árbol
fijado y probarlos exigiría añadirlo solo para una aplicación de juguete. En
ellos el `400` por campo de más es una obligación del contrato, vigilada por
`test_closure.py`, que cumplirán F4, F5, F8 y F12 con el mecanismo descrito en
`src/models/__init__.py`.
"""

from typing import Literal

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from src.core.errors import install_error_handlers
from tests.contract.harness import BASE_URL, assert_bare_error, assert_satisfies_contract

GALLERY_ID = "66f00000000000000000ffff"
GALLERIES = "/v1/fr/commercial/galleries"
IDENTITIES = f"{GALLERIES}/{GALLERY_ID}/identities"
DELIVERIES = "/v1/fr/commercial/webhooks/66f0000000000000000000aa/deliveries"
GALLERY = {
    "id": GALLERY_ID,
    "name": "Empleados",
    "model": "s",
    "status": "active",
    "identityCount": 3,
    "faceCount": 7,
    "createdAt": "2026-09-10T10:00:00Z",
}


class GalleryPatch(BaseModel):
    """Cuerpo de `updateGallery`, cerrado como lo está el contrato."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=500)


def _page(page: int, page_size: int) -> dict:
    return {"items": [], "page": page, "pageSize": page_size, "total": 0}


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    page_q = Query(1, ge=1)
    size_q = Query(20, ge=1, le=100)

    @app.get(GALLERIES)
    def list_galleries(page: int = page_q, pageSize: int = size_q):  # noqa: N803
        return _page(page, pageSize)

    @app.get(IDENTITIES.replace(GALLERY_ID, "{galleryId}"))
    def list_identities(galleryId: str, page: int = page_q, pageSize: int = size_q):  # noqa: N803
        return _page(page, pageSize)

    @app.get(DELIVERIES.replace("66f0000000000000000000aa", "{webhookId}"))
    def list_deliveries(
        webhookId: str,  # noqa: N803
        status: Literal["pending", "delivered", "failed"] | None = None,
        page: int = page_q,
        pageSize: int = size_q,  # noqa: N803
    ):
        return _page(page, pageSize)

    @app.patch(f"{GALLERIES}/{{galleryId}}")
    def update_gallery(galleryId: str, body: GalleryPatch):  # noqa: N803
        return GALLERY

    return app


@pytest.fixture
def client():
    with TestClient(make_app(), base_url=BASE_URL) as test_client:
        yield test_client


OUT_OF_RANGE = {
    "listGalleries pageSize=101": (GALLERIES, {"pageSize": 101}),
    "listGalleries pageSize=0": (GALLERIES, {"pageSize": 0}),
    "listGalleries page=0": (GALLERIES, {"page": 0}),
    "listIdentities pageSize=101": (IDENTITIES, {"pageSize": 101}),
    "listIdentities page=0": (IDENTITIES, {"page": 0}),
    "listWebhookDeliveries status=archived": (DELIVERIES, {"status": "archived"}),
    "listWebhookDeliveries pageSize=101": (DELIVERIES, {"pageSize": 101}),
    "listWebhookDeliveries page=0": (DELIVERIES, {"page": 0}),
}

IN_RANGE = {
    "listGalleries pageSize=100": (GALLERIES, {"pageSize": 100}),
    "listIdentities page=1": (IDENTITIES, {"page": 1}),
    "listWebhookDeliveries status=failed": (DELIVERIES, {"status": "failed"}),
}


@pytest.mark.parametrize("path,params", OUT_OF_RANGE.values(), ids=OUT_OF_RANGE.keys())
def test_a_parameter_out_of_range_is_a_declared_400(client, path, params):
    res = client.get(path, params=params)

    assert res.status_code == 400, res.text
    assert res.json() == {"code": "BAD_REQUEST", "message": "Invalid request"}
    assert_satisfies_contract(res)
    assert_bare_error(res)


@pytest.mark.parametrize("path,params", IN_RANGE.values(), ids=IN_RANGE.keys())
def test_the_same_parameter_in_range_is_a_conforming_200(client, path, params):
    res = client.get(path, params=params)

    assert res.status_code == 200, res.text
    assert_satisfies_contract(res)


def test_an_extra_field_in_a_json_body_is_a_declared_400(client):
    res = client.patch(f"{GALLERIES}/{GALLERY_ID}", json={"name": "x", "accountId": "otra"})

    assert res.status_code == 400, res.text
    assert res.json() == {"code": "BAD_REQUEST", "message": "Invalid request"}
    assert_satisfies_contract(res)
    assert_bare_error(res)


def test_the_same_body_without_the_extra_field_is_a_conforming_200(client):
    res = client.patch(f"{GALLERIES}/{GALLERY_ID}", json={"name": "x"})

    assert res.status_code == 200, res.text
    assert_satisfies_contract(res)
