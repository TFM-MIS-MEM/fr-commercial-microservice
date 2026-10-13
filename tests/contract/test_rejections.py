"""Rechazos que el contrato declara como `400`, con el manejador de errores real.

`listIdentities` y `listWebhookDeliveries` no están implementadas, así que se
sirven sobre una aplicación mínima en las rutas CANÓNICAS, con las cotas
copiadas del contrato y con `install_error_handlers`, que es lo que convierte la
validación en `400` `ErrorReport`. Es el patrón que heredan cuando se
implementen: cada cota del contrato, una petición fuera de rango.

`listGalleries` y `updateGallery` estuvieron aquí hasta F3 (#35). Hoy se prueban
sobre las operaciones reales, con los mismos casos y alguno más:
`test_list_galleries.py` y `test_update_gallery.py`.

Antes de docs#103 ningún listado declaraba `400`, y para `pageSize=101` no
existía implementación conforme: aceptar incumplía el `maximum` y rechazar
emitía un estado no declarado (medido en #33). Estas pruebas lo afirman en los
dos sentidos: fuera de rango, `400` conforme; dentro, `200` conforme.

ALCANCE: el campo de más en un cuerpo JSON se prueba sobre `createGallery` y
`updateGallery`. Los cuerpos `multipart/form-data` se prueban sobre las
operaciones reales a medida que se implementan: los tres de F5 en
`test_inference.py`. En los demás el `400` por campo de más es una obligación
del contrato, vigilada por `test_closure.py`, que cumplirán F4, F8 y F12 con el
mecanismo descrito en `src/models/__init__.py`.
"""

from typing import Literal

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from src.core.errors import install_error_handlers
from tests.contract.harness import BASE_URL, assert_bare_error, assert_satisfies_contract

GALLERY_ID = "66f00000000000000000ffff"
GALLERIES = "/v1/fr/commercial/galleries"
IDENTITIES = f"{GALLERIES}/{GALLERY_ID}/identities"
DELIVERIES = "/v1/fr/commercial/webhooks/66f0000000000000000000aa/deliveries"


def _page(page: int, page_size: int) -> dict:
    return {"items": [], "page": page, "pageSize": page_size, "total": 0}


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    page_q = Query(1, ge=1)
    size_q = Query(20, ge=1, le=100)

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

    return app


@pytest.fixture
def client():
    with TestClient(make_app(), base_url=BASE_URL) as test_client:
        yield test_client


OUT_OF_RANGE = {
    "listIdentities pageSize=101": (IDENTITIES, {"pageSize": 101}),
    "listIdentities page=0": (IDENTITIES, {"page": 0}),
    "listWebhookDeliveries status=archived": (DELIVERIES, {"status": "archived"}),
    "listWebhookDeliveries pageSize=101": (DELIVERIES, {"pageSize": 101}),
    "listWebhookDeliveries page=0": (DELIVERIES, {"page": 0}),
}

IN_RANGE = {
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
