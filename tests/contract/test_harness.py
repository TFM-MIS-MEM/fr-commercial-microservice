"""El arnés, probado contra un servicio mentiroso.

Un arnés que no puede fallar no prueba nada, y el de contrato es el que más fácil
lo tiene: una ruta mal resuelta, un anfitrión fuera de `servers` o un adaptador
que entregue el cuerpo vacío y todo «cumple». Por eso cada mentira de esta tabla
se sirve de verdad, por HTTP, en las rutas canónicas, y se afirma el veredicto
EN LOS DOS SENTIDOS: lo que el arnés caza y lo que deja pasar.

Las que deja pasar están escritas a propósito, cada una con su porqué. Si un día
cambian de veredicto, esta prueba se pone en rojo y obliga a reescribir el porqué.
La primera que lo hará es el campo de más: cuando el PR de docs#103 cierre los
esquemas del contrato (vía C del punto 3 de #33), openapi-core lo cazará y la
fila de «campo de más en Gallery» fallará sola.
"""

import json
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

from tests.contract.harness import BASE_URL, assert_bare_error, assert_satisfies_contract

GALLERY = "/v1/fr/commercial/galleries/66f00000000000000000ffff"
CONFORMING = {
    "id": "66f00000000000000000ffff",
    "name": "Empleados",
    "model": "s",
    "status": "active",
    "identityCount": 3,
    "faceCount": 7,
    "createdAt": "2026-09-10T10:00:00Z",
}
UNAUTHORIZED = {"code": "UNAUTHORIZED", "message": "No valid identity was provided"}


@dataclass(frozen=True)
class Lie:
    status: int
    body: object = None
    content_type: str = "application/json"
    method: str = "GET"

    def serve(self):
        if self.body is None:
            content = b""
        elif isinstance(self.body, bytes):
            content = self.body
        else:
            content = json.dumps(self.body).encode()
        app = FastAPI()

        # Respuesta de starlette a mano: el mentiroso ha de decir EXACTAMENTE lo
        # que la fila dice, sin que FastAPI filtre, serialice ni complete nada.
        @app.api_route(GALLERY, methods=[self.method])
        def lie():
            return Response(content, status_code=self.status, media_type=self.content_type)

        with TestClient(app, base_url=BASE_URL) as client:
            return client.request(self.method, GALLERY)


def contract_verdict(response) -> str:
    try:
        assert_satisfies_contract(response)
    except AssertionError:
        return "CAZA"
    return "pasa"


CAUGHT = {
    "frozenReason: null (docs#103: allOf + nullable sin type)": Lie(
        200, {**CONFORMING, "frozenReason": None}
    ),
    "falta un obligatorio (status)": Lie(
        200, {k: v for k, v in CONFORMING.items() if k != "status"}
    ),
    "enum fuera (status=archived)": Lie(200, {**CONFORMING, "status": "archived"}),
    "tipo (identityCount='3')": Lie(200, {**CONFORMING, "identityCount": "3"}),
    "formato (createdAt='ayer')": Lie(200, {**CONFORMING, "createdAt": "ayer"}),
    "200 sin cuerpo": Lie(200),
    "200 como text/plain": Lie(200, b"Empleados", content_type="text/plain"),
    "422 por omisión de FastAPI": Lie(422, {"detail": [{"loc": ["path"], "msg": "x"}]}),
    "400 que getGallery no declara": Lie(400, {"code": "BAD_REQUEST", "message": "x"}),
    "500 ErrorReport, no declarado": Lie(
        500, {"code": "INTERNAL_ERROR", "message": "Internal server error"}
    ),
    "401 con la forma de python-template": Lie(401, {"error": {"message": "x", "path": "/"}}),
    "401 como application/problem+json": Lie(
        401, UNAUTHORIZED, content_type="application/problem+json"
    ),
    "204 con cuerpo, donde no se declara content": Lie(204, {"deleted": True}, method="DELETE"),
}

PASSES = {
    "200 conforme": Lie(200, CONFORMING),
    "401 conforme": Lie(401, UNAUTHORIZED),
    "404 conforme": Lie(404, {"code": "NOT_FOUND", "message": "Gallery not found"}),
    "204 sin cuerpo": Lie(204, method="DELETE"),
    # PASA, y es lo correcto HOY: ningún esquema del contrato cierra
    # `additionalProperties`, así que un campo de más es conforme para cualquier
    # validador. Cerrarlos es la vía C del punto 3, y va en el PR de docs#103,
    # no en F1. Cuando llegue, ESTA FILA SE PONDRÁ EN ROJO SOLA: muévala a
    # CAUGHT en ese mismo PR.
    "campo de más en Gallery (accountId)": Lie(200, {**CONFORMING, "accountId": "66f0a1"}),
    # PASA para el contrato y queda así: `details` es abierto a propósito, y
    # `ErrorReport` se queda fuera de la vía C hasta docs#88, porque es idéntico
    # en los tres contratos. Lo caza `assert_bare_error`, más abajo.
    "traza en details (caso #79)": Lie(
        401, {**UNAUTHORIZED, "details": {"stack": "File /app/src/core/identity.py"}}
    ),
    # Ídem: ErrorReport sigue abierto. También lo caza `assert_bare_error`.
    "campo de más en ErrorReport": Lie(401, {**UNAUTHORIZED, "path": "/v1/fr/commercial"}),
}


@pytest.mark.parametrize("lie", CAUGHT.values(), ids=CAUGHT.keys())
def test_the_harness_catches(lie):
    assert contract_verdict(lie.serve()) == "CAZA"


@pytest.mark.parametrize("lie", PASSES.values(), ids=PASSES.keys())
def test_the_harness_lets_through(lie):
    assert contract_verdict(lie.serve()) == "pasa"


def test_the_harness_says_why():
    # Que falle no basta: el mensaje ha de llevar a la causa sin depurar.
    lie = CAUGHT["frozenReason: null (docs#103: allOf + nullable sin type)"]

    with pytest.raises(AssertionError) as caught:
        assert_satisfies_contract(lie.serve())

    message = str(caught.value)
    assert message.startswith(f"GET {GALLERY} -> 200 no cumple el contrato")
    assert "None is not of type 'string'" in message


def test_the_empty_body_check_names_the_body():
    with pytest.raises(AssertionError, match=r"no declara cuerpo y lleva b'\{\"deleted\""):
        assert_satisfies_contract(CAUGHT["204 con cuerpo, donde no se declara content"].serve())


@pytest.mark.parametrize(
    "lie,verdict",
    [
        (PASSES["401 conforme"], "pasa"),
        (PASSES["404 conforme"], "pasa"),
        (PASSES["traza en details (caso #79)"], "CAZA"),
        (PASSES["campo de más en ErrorReport"], "CAZA"),
    ],
    ids=["401 conforme", "404 conforme", "traza en details", "campo de más"],
)
def test_the_bare_error_check(lie, verdict):
    try:
        assert_bare_error(lie.serve())
    except AssertionError:
        assert verdict == "CAZA"
    else:
        assert verdict == "pasa"
