"""Manejador de errores, sobre una aplicación mínima.

El 500 se prueba aquí y no de extremo a extremo: ninguna operación del contrato
lo declara, y provocarlo por HTTP exigiría romper a propósito una dependencia.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from src.core.errors import ErrorReportError, install_error_handlers
from src.core.logger import setup_logger


class Body(BaseModel):
    name: str


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("fallo con /app/src/secreto.py y la cadena de conexión")

    @app.get("/http/{status}")
    async def http(status: int):
        raise HTTPException(status_code=status, detail="texto ajeno que no debe salir")

    @app.get("/domain")
    async def domain():
        raise ErrorReportError(403, "QUOTA_EXCEEDED", "Quota exceeded", {"limit": 500})

    @app.post("/validate")
    async def validate(body: Body):
        return body

    return app


@pytest.fixture
def client():
    setup_logger("info")
    return TestClient(make_app(), raise_server_exceptions=False)


def test_500_is_generic_and_carries_no_trace(client, caplog):
    with caplog.at_level(logging.ERROR):
        res = client.get("/boom")

    assert res.status_code == 500
    assert res.headers["content-type"] == "application/json"
    assert res.json() == {"code": "INTERNAL_ERROR", "message": "Internal server error"}
    # La traza va al registro, y solo allí.
    assert "Traceback" in caplog.text and "secreto.py" in caplog.text
    assert "secreto" not in res.text and "Traceback" not in res.text


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "BAD_REQUEST"),
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (405, "METHOD_NOT_ALLOWED"),
        (409, "CONFLICT"),
        (413, "REQUEST_ENTITY_TOO_LARGE"),
        (418, "IM_A_TEAPOT"),
        (422, "UNPROCESSABLE_ENTITY"),
        (429, "RATE_LIMIT_EXCEEDED"),
        (503, "INTERNAL_ERROR"),
    ],
)
def test_every_http_exception_is_an_error_report(client, status, code):
    res = client.get(f"/http/{status}")

    assert res.status_code == status
    assert res.headers["content-type"] == "application/json"
    body = res.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == code
    assert "texto ajeno" not in res.text


@pytest.mark.parametrize("status", range(400, 500))
def test_no_4xx_announces_an_internal_error(client, status):
    # #89: un 4xx con «Internal server error» o INTERNAL_ERROR miente sobre la
    # causa. Se recorren TODOS los estados 4xx, no una muestra.
    res = client.get(f"/http/{status}")

    assert res.status_code == status
    body = res.json()
    assert body["code"] != "INTERNAL_ERROR"
    assert "internal" not in body["message"].lower()


def test_validation_is_400_and_does_not_echo_the_input(client):
    res = client.post("/validate", json={"name": 12345, "password": "no-debe-volver"})

    assert res.status_code == 400
    assert res.json() == {"code": "BAD_REQUEST", "message": "Invalid request"}
    assert "no-debe-volver" not in res.text and "pydantic" not in res.text


def test_malformed_json_is_400(client):
    res = client.post("/validate", content=b"{", headers={"Content-Type": "application/json"})

    assert res.status_code == 400
    assert res.json()["code"] == "BAD_REQUEST"


def test_domain_errors_keep_their_code_and_details(client):
    res = client.get("/domain")

    assert res.status_code == 403
    assert res.json() == {
        "code": "QUOTA_EXCEEDED",
        "message": "Quota exceeded",
        "details": {"limit": 500},
    }
