"""Guardián de montaje: ninguna operación del contrato se sirve sin identidad.

Se comprueba POR COMPORTAMIENTO y no inspeccionando el enrutador. FastAPI 0.141
dejó de aplanar en `app.routes` las rutas de los routers incluidos (aparece un
`_IncludedRouter`, medido en #33), de modo que un guardián que recorra esas
estructuras se rompe al actualizar FastAPI, y lo haría en silencio: sin rutas
que recorrer, no encuentra ninguna sin proteger.

Y se ata al contrato por los dos lados, para que no pueda pasar vacío:

  - El contrato declara 27 operaciones y todas con `security`. Si ese número
    cambia, esta prueba lo dice.
  - El conjunto de operaciones implementadas es EXACTAMENTE el esperado. Hoy,
    `{getGallery}`. Una operación nueva obliga a tocar esta lista, y con ello a
    que la prueba de 401 la recorra.
"""

import re
from pathlib import Path

import pytest
import yaml

from src.core.settings import Settings
from src.main import create_app

SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "openapi" / "fr-commercial.yaml").read_text()
)
METHODS = ("get", "post", "put", "patch", "delete")

IMPLEMENTED = {("get", "/v1/fr/commercial/galleries/{galleryId}")}


def contract_operations():
    return {
        (method, path): op
        for path, item in SPEC["paths"].items()
        for method, op in item.items()
        if method in METHODS
    }


def served_operations():
    app = create_app(Settings(mongodb_uri="mongodb://unused.invalid:27017/unused"))
    generated = app.openapi()
    return {(method, path) for path, item in generated["paths"].items() for method in item}


def test_the_contract_declares_27_operations_all_with_security():
    ops = contract_operations()

    assert len(ops) == 27
    assert [key for key, op in ops.items() if not op.get("security")] == []


def test_the_service_implements_exactly_the_expected_operations():
    assert served_operations() == IMPLEMENTED


def test_every_served_operation_is_in_the_contract():
    assert served_operations() <= set(contract_operations())


@pytest.mark.parametrize("method,path", sorted(IMPLEMENTED))
def test_every_implemented_operation_rejects_a_request_without_identity(client, method, path):
    concrete = re.sub(r"\{[^}]+\}", "66f00000000000000000ffff", path)

    res = client.request(method.upper(), concrete)

    assert res.status_code == 401, res.text
    assert res.json()["code"] == "UNAUTHORIZED"
