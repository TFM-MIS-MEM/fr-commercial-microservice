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
    `{getGallery}`. Una operación nueva obliga a tocar `harness.IMPLEMENTED`, y
    con ello a que la prueba de 401 y la comparación estática la recorran.

PARA F3: EL GUARDIÁN SOLO VE LO QUE SALE EN `app.openapi()`. Una operación
montada con `include_in_schema=False` no aparece ahí, así que ni rompe la
igualdad con `IMPLEMENTED` ni entra en la prueba de 401. Seguiría protegida
---la dependencia está en el router---, pero ninguna prueba lo afirmaría. Con una
ruta da igual; con veinte, no. Ninguna operación del contrato se monta así.
"""

import re

import pytest

from src.core.settings import Settings
from src.main import create_app
from tests.contract.harness import IMPLEMENTED, SPEC, assert_bare_error, assert_satisfies_contract

METHODS = ("get", "post", "put", "patch", "delete")


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
    assert_satisfies_contract(res)
    assert_bare_error(res)
