"""El arnés, probado contra un servicio mentiroso.

Un arnés que no puede fallar no prueba nada, y el de contrato es el que más fácil
lo tiene: una ruta mal resuelta, un anfitrión fuera de `servers` o un adaptador
que entregue el cuerpo vacío y todo «cumple». Por eso cada mentira de esta tabla
se sirve de verdad, por HTTP, en las rutas canónicas, y se afirma el veredicto
EN LOS DOS SENTIDOS: lo que el arnés caza y lo que deja pasar.

Las que deja pasar están escritas a propósito, cada una con su porqué. Si un día
cambian de veredicto, esta prueba se pone en rojo y obliga a reescribir el porqué.
Ya ha pasado una vez: el campo de más en `Gallery` estaba entre las que pasan
hasta que docs#103 cerró los esquemas del contrato (vía C del punto 3 de #33).
La fila se puso en rojo sola, contra el contrato nuevo y sin tocarla, y se movió
a las que se cazan en ese mismo PR.

Las del secreto de un punto de notificación son la prueba de que el cierre
sirvió: hoy lo caza openapi-core, sin una línea de código nuestro; con el
contrato anterior, las tres pasaban.
"""

import json
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

from tests.contract.harness import BASE_URL, assert_bare_error, assert_satisfies_contract

GALLERY = "/v1/fr/commercial/galleries/66f00000000000000000ffff"
WEBHOOKS = "/v1/fr/commercial/webhooks"
WEBHOOK = f"{WEBHOOKS}/66f0000000000000000000aa"
CONFORMING = {
    "id": "66f00000000000000000ffff",
    "name": "Empleados",
    "model": "s",
    "status": "active",
    "identityCount": 3,
    "faceCount": 7,
    "createdAt": "2026-09-10T10:00:00Z",
}
ENDPOINT = {
    "id": "66f0000000000000000000aa",
    "url": "https://hooks.example.com/fr",
    "events": ["gallery.frozen"],
    "status": "active",
    "consecutiveFailures": 0,
    "lastDeliveryAt": None,
    "createdAt": "2026-09-30T10:00:00Z",
}
SECRET = "whsec_4f1c9e0b7a"
UNAUTHORIZED = {"code": "UNAUTHORIZED", "message": "No valid identity was provided"}


@dataclass(frozen=True)
class Lie:
    status: int
    body: object = None
    content_type: str = "application/json"
    method: str = "GET"
    path: str = GALLERY

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
        @app.api_route(self.path, methods=[self.method])
        def lie():
            return Response(content, status_code=self.status, media_type=self.content_type)

        with TestClient(app, base_url=BASE_URL) as client:
            return client.request(self.method, self.path)


def contract_verdict(response) -> str:
    try:
        assert_satisfies_contract(response)
    except AssertionError:
        return "CAZA"
    return "pasa"


CAUGHT = {
    # Antes de docs#103 lo cazaba openapi-core y Schemathesis no, por el
    # `nullable` sin `type`. Hoy `frozenReason` no admite `null` en ninguna
    # lectura: se omite cuando la galería no está congelada.
    "frozenReason: null (no admite null)": Lie(200, {**CONFORMING, "frozenReason": None}),
    # PASABA hasta docs#103, cuando ningún esquema cerraba `additionalProperties`.
    # Se puso en rojo sola contra el contrato cerrado y se movió aquí.
    "campo de más en Gallery (accountId)": Lie(200, {**CONFORMING, "accountId": "66f0a1"}),
    # El secreto solo viaja en el 201 de createWebhook. Devuelto por una
    # consulta, las tres pasaban con el contrato abierto; hoy las caza el
    # cierre de WebhookEndpoint.
    "getWebhook con el secreto": Lie(200, {**ENDPOINT, "secret": SECRET}, path=WEBHOOK),
    "listWebhooks con el secreto": Lie(200, [{**ENDPOINT, "secret": SECRET}], path=WEBHOOKS),
    "updateWebhook con el secreto": Lie(
        200, {**ENDPOINT, "secret": SECRET}, method="PATCH", path=WEBHOOK
    ),
    "201 de createWebhook con un campo de más": Lie(
        201, {**ENDPOINT, "secret": SECRET, "accountId": "66f0a1"}, method="POST", path=WEBHOOKS
    ),
    "201 de createWebhook sin el secreto": Lie(201, ENDPOINT, method="POST", path=WEBHOOKS),
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
    "getWebhook conforme": Lie(200, ENDPOINT, path=WEBHOOK),
    # El 201 legítimo, con su secreto: el cierre del compuesto no lo rechaza.
    "201 de createWebhook con su secreto": Lie(
        201, {**ENDPOINT, "secret": SECRET}, method="POST", path=WEBHOOKS
    ),
    # PASA para el contrato y queda así: `details` es abierto a propósito, y
    # `ErrorReport` es el único esquema propio que docs#103 dejó abierto, hasta
    # docs#88, porque es idéntico en los tres contratos. Lo caza
    # `assert_bare_error`, más abajo.
    "traza en details (caso #79)": Lie(
        401, {**UNAUTHORIZED, "details": {"stack": "File /app/src/core/identity.py"}}
    ),
    # Ídem, y por la misma razón: ErrorReport sigue abierto tras docs#103,
    # hasta docs#88. Cuando se cierre, esta fila se pondrá roja sola, como la
    # del campo de más en Gallery. Hoy lo caza `assert_bare_error`.
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
    lie = CAUGHT["frozenReason: null (no admite null)"]

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
