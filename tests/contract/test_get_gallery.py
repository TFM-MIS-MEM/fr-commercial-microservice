"""getGallery — GET /v1/fr/commercial/galleries/{galleryId}.

La operación con la que F1 demuestra la cadena de identidad de extremo a
extremo: RETRIEVAL, cero unidades, sin plan ni Redis ni parámetros de consulta,
y con `X-Account-Id` como lo ÚNICO que decide entre `200` y `404`.

CADA 404 VA EMPAREJADO CON EL 200 DEL PROPIETARIO. Un `404` a secas pasaría
también si la siembra no hubiera escrito nada, si la consulta estuviera rota o si
las cabeceras no llegaran: sería una comprobación que no puede fallar. El
emparejamiento hace que la misma galería, pedida por su dueño, responda `200` en
la misma prueba, de modo que el `404` solo puede deberse a la cuenta.
"""

from bson import ObjectId

from tests.support.identity import ABSENT, ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import CREATED, seed_gallery

PATH = "/v1/fr/commercial/galleries/{}"
DECLARED = {"id", "name", "model", "status", "identityCount", "faceCount", "createdAt"}


def get(client, gallery_id, headers):
    return client.get(PATH.format(gallery_id), headers=headers)


def assert_owner_sees_it(client, gallery_id):
    owner = get(client, gallery_id, gateway_headers(ACCOUNT_A))
    assert owner.status_code == 200, owner.text
    assert owner.json()["id"] == gallery_id


def assert_not_found(response):
    assert response.status_code == 404, response.text
    assert response.json() == {"code": "NOT_FOUND", "message": "Gallery not found"}


# ---------------------------------------------------------------- 200


def test_200_the_owner_gets_its_gallery(client, db):
    gallery_id = seed_gallery(db, ACCOUNT_A)

    res = get(client, gallery_id, gateway_headers(ACCOUNT_A))

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/json"
    body = res.json()
    assert body == {
        "id": gallery_id,
        "name": "Empleados",
        "model": "s",
        "status": "active",
        "identityCount": 3,
        "faceCount": 7,
        "createdAt": CREATED.isoformat().replace("+00:00", "Z"),
    }


def test_200_the_api_channel_gets_exactly_the_same_contract(client, db):
    # Regla 1 de CLAUDE.md: el canal no se manifiesta en el recurso.
    gallery_id = seed_gallery(db, ACCOUNT_A)

    web = get(client, gallery_id, gateway_headers(ACCOUNT_A, channel="frontend"))
    api = get(client, gallery_id, gateway_headers(ACCOUNT_A, channel="api"))

    assert web.status_code == api.status_code == 200
    assert web.json() == api.json()


def test_200_does_not_return_the_owner_nor_the_mongo_id(client, db):
    gallery_id = seed_gallery(db, ACCOUNT_A, internalNote="no sale")

    body = get(client, gallery_id, gateway_headers(ACCOUNT_A)).json()

    assert set(body) == DECLARED
    assert "accountId" not in body and "_id" not in body


def test_200_optional_fields_are_omitted_one_by_one_not_sent_as_null(client, db):
    # frozenReason, description y updatedAt no admiten null en el contrato
    # (docs#103): se omiten cuando no hay valor.
    gallery_id = seed_gallery(db, ACCOUNT_A)

    body = get(client, gallery_id, gateway_headers(ACCOUNT_A)).json()

    for name in ("frozenReason", "description", "updatedAt"):
        assert name not in body


def test_200_a_frozen_gallery_carries_its_reason(client, db):
    gallery_id = seed_gallery(
        db,
        ACCOUNT_A,
        status="frozen",
        frozenReason="MODEL_NOT_IN_PLAN",
        description="congelada",
        updatedAt=CREATED,
    )

    body = get(client, gallery_id, gateway_headers(ACCOUNT_A)).json()

    assert body["status"] == "frozen"
    assert body["frozenReason"] == "MODEL_NOT_IN_PLAN"
    assert body["description"] == "congelada"
    assert body["updatedAt"] == "2026-09-10T10:00:00Z"


# ---------------------------------------------------------------- 404


def test_404_another_account_cannot_see_the_gallery(client, db):
    gallery_id = seed_gallery(db, ACCOUNT_A)

    assert_not_found(get(client, gallery_id, gateway_headers(ACCOUNT_B)))
    assert_owner_sees_it(client, gallery_id)


def test_404_an_admin_of_another_account_cannot_see_it_either(client, db):
    # ADMIN no amplía el alcance: la propiedad es de la cuenta. El porqué del 404
    # y no 403 está en src/controllers/galleries.py.
    gallery_id = seed_gallery(db, ACCOUNT_A)

    assert_not_found(get(client, gallery_id, gateway_headers(ACCOUNT_B, role="ADMIN")))
    assert_owner_sees_it(client, gallery_id)


def test_404_a_gallery_that_does_not_exist(client, db):
    existing = seed_gallery(db, ACCOUNT_A)

    assert_not_found(get(client, str(ObjectId()), gateway_headers(ACCOUNT_A)))
    assert_owner_sees_it(client, existing)


def test_404_an_identifier_that_cannot_exist(client, db):
    # getGallery no declara 400: un identificador imposible es una galería que
    # no existe.
    existing = seed_gallery(db, ACCOUNT_A)

    assert_not_found(get(client, "no-es-un-objectid", gateway_headers(ACCOUNT_A)))
    assert_owner_sees_it(client, existing)


def test_404_is_identical_for_foreign_and_nonexistent(client, db):
    # Si difirieran, el 404 sería un oráculo de qué galerías tienen otras cuentas.
    foreign = seed_gallery(db, ACCOUNT_B)

    a = get(client, foreign, gateway_headers(ACCOUNT_A))
    b = get(client, str(ObjectId()), gateway_headers(ACCOUNT_A))

    assert (a.status_code, a.json()) == (b.status_code, b.json())
    res = get(client, foreign, gateway_headers(ACCOUNT_B))
    assert res.status_code == 200


# ---------------------------------------------------------------- 401

INVALID_IDENTITIES = {
    "sin ninguna cabecera": [],
    "sin X-Account-Id": gateway_headers(account_id=ABSENT),
    "sin X-Auth-Channel": gateway_headers(channel=ABSENT),
    "sin X-Role": gateway_headers(role=ABSENT),
    "X-Account-Id en blanco": gateway_headers(account_id="   "),
    "X-Auth-Channel fuera del conjunto": gateway_headers(channel="mobile"),
    "X-Role fuera del conjunto": gateway_headers(role="RESEARCHER"),
    "X-Role en minúsculas": gateway_headers(role="customer"),
    "X-Account-Id repetida": gateway_headers(extra=[("X-Account-Id", ACCOUNT_B)]),
    "X-Auth-Channel repetida": gateway_headers(extra=[("X-Auth-Channel", "api")]),
    "X-Role repetida (CUSTOMER+ADMIN)": gateway_headers(extra=[("X-Role", "ADMIN")]),
    "X-Role repetida (ADMIN+CUSTOMER)": gateway_headers(role="ADMIN") + [("X-Role", "CUSTOMER")],
    "X-Role con dos valores en una cabecera": gateway_headers(role="CUSTOMER, ADMIN"),
}


def pytest_generate_tests(metafunc):
    if "invalid_identity" in metafunc.fixturenames:
        metafunc.parametrize(
            "invalid_identity",
            list(INVALID_IDENTITIES.values()),
            ids=list(INVALID_IDENTITIES),
        )


def test_401_invalid_identity_is_rejected_even_for_the_owners_gallery(client, db, invalid_identity):
    # La galería existe y es de ACCOUNT_A, que es la cuenta que declaran casi
    # todos los casos: el 401 no puede deberse a la propiedad.
    gallery_id = seed_gallery(db, ACCOUNT_A)

    res = get(client, gallery_id, invalid_identity)

    assert res.status_code == 401, res.text
    assert res.headers["content-type"] == "application/json"
    assert res.json() == {"code": "UNAUTHORIZED", "message": "No valid identity was provided"}
    assert_owner_sees_it(client, gallery_id)


def test_401_comes_before_404(client, db):
    # Sin identidad no hay propiedad que comprobar: no se revela si existe.
    res = get(client, str(ObjectId()), [])

    assert res.status_code == 401
