"""updateIdentity — PATCH …/galleries/{galleryId}/identities/{identityId} (F4, #36).

Toda respuesta pasa por el arnés (`tests/support/identities.checked`).

Las reglas de `updateGallery` (F3): un cuerpo vacío no escribe nada; `null` es
`400` porque ningún campo del cuerpo declara `nullable` ---en consecuencia, un
`externalId` dado no se puede quitar por esta operación---; el `409` sale del
índice y no de una consulta previa; y se permite sobre una galería congelada.

CADA RECHAZO COMPRUEBA QUE NO SE ESCRIBIÓ NADA: el documento es el de antes.
"""

from datetime import datetime

import pytest
from bson import ObjectId

from tests.support.identities import (
    assert_bad_request,
    assert_not_found,
    fetch,
    identity_doc,
    update,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import seed_gallery, seed_identity_with_faces


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A)


@pytest.fixture
def identity(db, gallery):
    return seed_identity_with_faces(db, gallery, faces=2, externalId="E-1")


# ---------------------------------------------------------------- 200


def test_200_renaming_changes_fullName_and_sets_updatedAt(client, db, gallery, identity):
    before = fetch(client, gallery, identity).json()

    res = update(client, gallery, identity, {"fullName": "Eva"})

    assert res.status_code == 200, res.text
    after = res.json()
    assert after["fullName"] == "Eva"
    assert datetime.fromisoformat(after["updatedAt"]) >= datetime.fromisoformat(before["createdAt"])
    unchanged = {k: v for k, v in after.items() if k not in ("fullName", "updatedAt")}
    assert unchanged == {k: v for k, v in before.items() if k != "fullName"}
    assert fetch(client, gallery, identity).json() == after


def test_200_sets_an_externalId_where_there_was_none(client, db, gallery):
    identity = seed_identity_with_faces(db, gallery, faces=1)

    body = update(client, gallery, identity, {"externalId": "E-2"}).json()

    assert body["externalId"] == "E-2"
    assert identity_doc(db, identity)["externalId"] == "E-2"


def test_200_an_empty_body_writes_nothing(client, db, gallery, identity):
    before = identity_doc(db, identity)

    res = update(client, gallery, identity, {})

    assert res.status_code == 200, res.text
    assert "updatedAt" not in res.json()
    assert identity_doc(db, identity) == before


def test_200_its_own_externalId_is_not_a_conflict(client, db, gallery, identity):
    assert update(client, gallery, identity, {"externalId": "E-1"}).status_code == 200


def test_200_an_externalId_used_in_another_gallery(client, db, gallery, identity):
    other = seed_gallery(db, ACCOUNT_A, name="Otra")
    seed_identity_with_faces(db, other, faces=1, externalId="E-2")

    assert update(client, gallery, identity, {"externalId": "E-2"}).status_code == 200


def test_200_on_a_frozen_gallery(client, db):
    frozen = seed_gallery(db, ACCOUNT_A, status="frozen", frozenReason="MODEL_NOT_IN_PLAN")
    identity = seed_identity_with_faces(db, frozen, faces=1)

    assert update(client, frozen, identity, {"fullName": "Eva"}).status_code == 200


# ---------------------------------------------------------------- 409


def test_409_the_externalId_of_another_identity_of_the_gallery(client, db, gallery, identity):
    other = seed_identity_with_faces(db, gallery, faces=1, externalId="E-2")
    before = identity_doc(db, other)

    res = update(client, gallery, other, {"externalId": "E-1"})

    assert res.status_code == 409, res.text
    assert res.json()["code"] == "EXTERNAL_ID_ALREADY_EXISTS"
    assert identity_doc(db, other) == before


# ---------------------------------------------------------------- 400

REJECTED = {
    "fullName null": {"fullName": None},
    "externalId null": {"externalId": None},
    "externalId vacío": {"externalId": ""},
    "fullName no es cadena": {"fullName": 7},
    "campo de más": {"fullName": "Eva", "galleryId": "x"},
    "faceCount no es de entrada": {"faceCount": 9},
}


@pytest.mark.parametrize("body", REJECTED.values(), ids=REJECTED.keys())
def test_400_rejected_and_nothing_written(client, db, gallery, identity, body):
    before = identity_doc(db, identity)

    assert_bad_request(update(client, gallery, identity, body))
    assert identity_doc(db, identity) == before


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, db, gallery, identity, role):
    before = identity_doc(db, identity)

    res = update(
        client, gallery, identity, {"fullName": "Eva"}, gateway_headers(ACCOUNT_B, role=role)
    )

    assert_not_found(res, "Gallery")
    assert identity_doc(db, identity) == before


def test_404_an_identity_of_another_gallery_of_the_same_account(client, db, identity):
    other = seed_gallery(db, ACCOUNT_A, name="Otra")
    before = identity_doc(db, identity)

    assert_not_found(update(client, other, identity, {"fullName": "Eva"}), "Identity")
    assert identity_doc(db, identity) == before


@pytest.mark.parametrize("identity_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_an_identity_that_does_not_or_cannot_exist(client, db, gallery, identity_id):
    assert_not_found(update(client, gallery, identity_id, {"fullName": "Eva"}), "Identity")
