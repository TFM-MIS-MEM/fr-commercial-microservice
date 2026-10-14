"""createGallery — POST /v1/fr/commercial/galleries (F3, #35).

Toda respuesta pasa por el arnés (`tests/support/galleries.checked`).

LAS COTAS DEL CUERPO, EN LOS DOS LADOS. La comparación estática no compara
cuerpos de petición (decidido en #35, modo entrega), así que cada cota del
contrato se prueba con una petición justo dentro y otra justo fuera. Y cada
cuerpo inválido es el válido con UN defecto: el válido, solo, da `201`, de modo
que el `400` solo puede deberse a ese defecto.

PROVISIONAL HASTA F10: sin plan conectado, no se emiten los `403` de plan y sin
`model` se usa `xs`. El porqué, en `src/controllers/galleries.py`.
"""

from datetime import datetime

import pytest
from bson import ObjectId

from tests.support.galleries import (
    REQUIRED,
    assert_bad_request,
    assert_name_conflict,
    create,
    fetch,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers

VALID = {"name": "Empleados"}


def stored(db):
    return list(db.galleries.find({}, {"_id": 0, "name": 1, "accountId": 1}))


# ---------------------------------------------------------------- 201


def test_201_the_minimal_body_creates_an_active_xs_gallery_with_zero_counters(client, db):
    res = create(client, VALID)

    assert res.status_code == 201, res.text
    assert res.headers["content-type"] == "application/json"
    body = res.json()
    assert set(body) == REQUIRED
    assert ObjectId.is_valid(body["id"])
    assert body["name"] == "Empleados"
    assert body["model"] == "xs"
    assert body["status"] == "active"
    assert (body["identityCount"], body["faceCount"]) == (0, 0)
    assert datetime.fromisoformat(body["createdAt"]).tzinfo is not None


def test_201_the_gallery_belongs_to_the_account_in_the_header(client, db):
    gallery_id = create(client, VALID, gateway_headers(ACCOUNT_B)).json()["id"]

    doc = db.galleries.find_one({"_id": ObjectId(gallery_id)})
    assert doc["accountId"] == ACCOUNT_B
    assert "description" not in doc and "updatedAt" not in doc and "frozenReason" not in doc


def test_201_what_is_returned_is_exactly_what_a_later_read_returns(client, db):
    # `createdAt` incluido: se trunca a milisegundos al crearlo, que es lo que
    # guarda MongoDB (src/documents/base.py).
    created = create(client, {**VALID, "description": "plantilla", "model": "s"}).json()

    read = fetch(client, created["id"]).json()

    assert read == created


@pytest.mark.parametrize("model", ["xs", "s", "base"])
def test_201_an_explicit_model_is_kept_and_read_back(client, db, model):
    created = create(client, {**VALID, "model": model})

    assert created.status_code == 201, created.text
    assert created.json()["model"] == model
    assert fetch(client, created.json()["id"]).json()["model"] == model


def test_201_the_api_channel_gets_the_same_contract(client, db):
    web = create(client, {"name": "web"}, gateway_headers(channel="frontend"))
    api = create(client, {"name": "api"}, gateway_headers(channel="api"))

    assert web.status_code == api.status_code == 201
    assert set(web.json()) == set(api.json()) == REQUIRED


def test_201_an_empty_name_is_accepted_because_the_contract_allows_it(client, db):
    # `name` no declara `minLength` (decidido en #35; la propuesta de cambio de
    # contrato es un issue con `alcance:futuro`).
    res = create(client, {"name": ""})

    assert res.status_code == 201, res.text
    assert res.json()["name"] == ""


# -------------------------------------------- las cotas, en los dos lados

BOUNDS = {
    "name 120": ({"name": "n" * 120}, 201),
    "name 121": ({"name": "n" * 121}, 400),
    "description 500": ({**VALID, "description": "d" * 500}, 201),
    "description 501": ({**VALID, "description": "d" * 501}, 400),
}


@pytest.mark.parametrize("body,status", BOUNDS.values(), ids=BOUNDS.keys())
def test_each_length_bound_on_both_sides(client, db, body, status):
    res = create(client, body)

    assert res.status_code == status, res.text
    assert len(stored(db)) == (1 if status == 201 else 0)


# ---------------------------------------------------------------- 400

INVALID = {
    "sin name": {"description": "sin nombre"},
    "status, que no es de entrada": {**VALID, "status": "frozen"},
    "un campo de más (accountId)": {**VALID, "accountId": ACCOUNT_B},
    "un campo de más (identityCount)": {**VALID, "identityCount": 9},
    "model fuera del enum": {**VALID, "model": "xl"},
    "name null": {"name": None},
    "description null": {**VALID, "description": None},
    "model null": {**VALID, "model": None},
    "name que no es cadena": {"name": 123},
}


def test_the_valid_body_that_every_invalid_one_derives_from_is_a_201(client, db):
    assert create(client, VALID).status_code == 201


@pytest.mark.parametrize("body", INVALID.values(), ids=INVALID.keys())
def test_400_an_invalid_body_is_rejected_and_nothing_is_written(client, db, body):
    res = create(client, body)

    assert_bad_request(res)
    assert stored(db) == []


def test_400_a_body_that_is_not_json(client, db):
    res = client.post(
        "/v1/fr/commercial/galleries",
        content=b"name=Empleados",
        headers=gateway_headers() + [("Content-Type", "application/json")],
    )

    assert_bad_request(res)
    assert stored(db) == []


def test_401_comes_before_400(client, db):
    # Sin identidad no se valida el cuerpo: la dependencia está en el router.
    res = client.post("/v1/fr/commercial/galleries", json={"model": "xl"})

    assert res.status_code == 401, res.text
    assert stored(db) == []


# ---------------------------------------------------------------- 409


def test_409_a_repeated_name_in_the_same_account(client, db):
    assert create(client, VALID).status_code == 201

    assert_name_conflict(create(client, {**VALID, "description": "otra"}))
    assert stored(db) == [{"accountId": ACCOUNT_A, "name": "Empleados"}]


def test_201_the_same_name_in_another_account_is_not_a_conflict(client, db):
    assert create(client, VALID, gateway_headers(ACCOUNT_A)).status_code == 201

    assert create(client, VALID, gateway_headers(ACCOUNT_B)).status_code == 201


def test_201_the_comparison_is_exact_case_and_accents(client, db):
    # `ConflictGalleryName`: «Empleados» y «empleados» son dos nombres distintos.
    for name in ("Empleados", "empleados", "Empleádos"):
        assert create(client, {"name": name}).status_code == 201

    assert_name_conflict(create(client, {"name": "empleados"}))
