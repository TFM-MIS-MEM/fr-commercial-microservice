"""getIdentity — GET …/galleries/{galleryId}/identities/{identityId} (F4, #36).

Toda respuesta pasa por el arnés (`tests/support/identities.checked`).

`externalId` y `updatedAt` se OMITEN cuando no hay valor: el contrato declara
`externalId` como `nullable`, pero el servicio nunca emite `null`, porque lo
guarda ausente. Cada `404` va emparejado con el `200` del propietario.
"""

import pytest
from bson import ObjectId

from tests.support.identities import IDENTITY_REQUIRED, assert_not_found, enroll, fetch
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import CREATED, seed_gallery, seed_identity_with_faces


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A, model="xs")


@pytest.fixture
def identity(db, gallery):
    return seed_identity_with_faces(db, gallery, faces=2)


# ---------------------------------------------------------------- 200


def test_200_without_the_optionals_they_are_omitted_never_null(client, db, gallery, identity):
    res = fetch(client, gallery, identity)

    assert res.status_code == 200, res.text
    assert res.json() == {
        "id": identity,
        "galleryId": gallery,
        "fullName": "Ana",
        "faceCount": 2,
        "createdAt": "2026-09-10T10:00:00Z",
    }


def test_200_with_the_optionals(client, db, gallery):
    identity = seed_identity_with_faces(db, gallery, faces=1, externalId="E-1", updatedAt=CREATED)

    body = fetch(client, gallery, identity).json()

    assert set(body) == IDENTITY_REQUIRED | {"externalId", "updatedAt"}
    assert body["externalId"] == "E-1"


def test_200_is_what_createIdentity_returned(client, db, gallery):
    created = enroll(client, gallery, {"fullName": "Eva", "externalId": "E-9"}).json()

    assert fetch(client, gallery, created["id"]).json() == created


def test_200_on_a_frozen_gallery(client, db):
    frozen = seed_gallery(db, ACCOUNT_A, status="frozen", frozenReason="MODEL_NOT_IN_PLAN")
    identity = seed_identity_with_faces(db, frozen, faces=1)

    assert fetch(client, frozen, identity).status_code == 200


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, db, gallery, identity, role):
    assert_not_found(
        fetch(client, gallery, identity, gateway_headers(ACCOUNT_B, role=role)), "Gallery"
    )
    assert fetch(client, gallery, identity).status_code == 200


def test_404_an_identity_of_another_gallery_of_the_same_account(client, db, identity):
    other = seed_gallery(db, ACCOUNT_A, name="Otra")

    assert_not_found(fetch(client, other, identity), "Identity")


@pytest.mark.parametrize("identity_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_an_identity_that_does_not_or_cannot_exist(client, db, gallery, identity_id):
    assert_not_found(fetch(client, gallery, identity_id), "Identity")


@pytest.mark.parametrize("gallery_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_gallery_that_does_not_or_cannot_exist(client, db, identity, gallery_id):
    assert_not_found(fetch(client, gallery_id, identity), "Gallery")
