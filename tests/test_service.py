"""Superficie del servicio que no está en el contrato: /docs, /openapi.json, /health.

No es una suite de contrato, pero cubre tres promesas que hay que sostener:

  1. La documentación servida ES el fichero de contrato, no una segunda
     descripción generada desde el código (criterio de C2).
  2. El alcance de la exención de CSP, en los DOS sentidos. Sin el segundo, nada
     impide que la exención de `/docs` se desborde a todo el servicio.
  3. `/health` es alcanzable sin identidad; si no, el orquestador reiniciaría el
     contenedor en bucle.
"""

import pytest

from src.api.docs import SPEC_PATH, load_spec
from tests.support.identity import gateway_headers


def test_openapi_json_is_exactly_the_contract_file(client):
    res = client.get("/openapi.json")

    assert res.status_code == 200
    assert res.json() == load_spec(SPEC_PATH)
    assert res.json()["info"]["title"].startswith("Servicio de Reconocimiento Facial")


def test_docs_serves_the_ui_pointing_to_the_contract(client):
    res = client.get("/docs")

    assert res.status_code == 200
    assert "swagger-ui" in res.text
    assert "/openapi.json" in res.text


def test_docs_goes_out_without_content_security_policy(client):
    res = client.get("/docs")

    assert "content-security-policy" not in res.headers
    # La exención es SOLO de la CSP.
    assert res.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/health", []),
        ("/openapi.json", []),
        ("/v1/fr/commercial/galleries/66f00000000000000000ffff", []),
        ("/v1/fr/commercial/galleries/66f00000000000000000ffff", gateway_headers()),
        ("/no-existe", []),
    ],
    ids=["health", "openapi.json", "401", "404", "ruta inexistente"],
)
def test_everything_else_goes_out_with_content_security_policy(client, path, headers):
    res = client.get(path, headers=headers)

    assert "default-src 'self'" in res.headers["content-security-policy"]


def test_health_is_reachable_without_identity(client):
    res = client.get("/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "connected"}


def test_an_unknown_route_is_an_error_report(client):
    res = client.get("/no-existe")

    assert res.status_code == 404
    assert res.json() == {"code": "NOT_FOUND", "message": "Not Found"}


def test_a_method_not_allowed_is_an_error_report_and_keeps_allow(client):
    res = client.delete("/health")

    assert res.status_code == 405
    assert res.json() == {"code": "METHOD_NOT_ALLOWED", "message": "Method Not Allowed"}
    assert "GET" in res.headers["allow"]
