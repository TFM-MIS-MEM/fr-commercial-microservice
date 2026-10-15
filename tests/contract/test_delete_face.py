"""deleteFace — DELETE …/identities/{identityId}/faces/{faceId} (F4, #36).

Toda respuesta pasa por el arnés (`tests/support/identities.checked`).

`409 LAST_FACE_OF_IDENTITY`: una identidad no se queda sin rostros. Sale de un
decremento CONDICIONAL sobre la identidad (`faceCount > 1`), no de una lectura
previa; la prueba de que eso cierra la carrera de dos borrados concurrentes está
en `test_races.py`.

LA CADENA llega hasta el rostro: un rostro de otra identidad, aunque sea de la
misma galería, es un rostro que no existe aquí.

Permitida sobre una galería congelada: no declara `403` de dominio, y congelar
bloquea enrolar, verificar y buscar (criterio de F3).
"""

import pytest
from bson import ObjectId

from tests.support.identities import (
    assert_not_found,
    delete_face,
    faces_of,
    identity_doc,
    state,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import seed_gallery, seed_identity_with_faces


@pytest.fixture
def gallery(db):
    # Dos identidades de 2 rostros: la de la prueba y una vecina.
    return seed_gallery(db, ACCOUNT_A, identityCount=2, faceCount=4)


@pytest.fixture
def identity(db, gallery):
    return seed_identity_with_faces(db, gallery, faces=2)


@pytest.fixture
def neighbour(db, gallery):
    return seed_identity_with_faces(db, gallery, faces=2)


def face_ids(db, identity_id):
    return [str(f["_id"]) for f in faces_of(db, identity_id)]


# ---------------------------------------------------------------- 204


def test_204_deletes_the_face_and_discounts_it(client, db, gallery, identity, neighbour):
    first, second = face_ids(db, identity)

    res = delete_face(client, gallery, identity, first)

    assert res.status_code == 204, res.text
    assert face_ids(db, identity) == [second]
    assert identity_doc(db, identity)["faceCount"] == 1
    assert state(db, gallery) == (2, 3, 2, 3)
    assert len(face_ids(db, neighbour)) == 2


def test_204_on_a_frozen_gallery(client, db):
    frozen = seed_gallery(db, ACCOUNT_A, status="frozen", frozenReason="MODEL_NOT_IN_PLAN")
    identity = seed_identity_with_faces(db, frozen, faces=2)

    assert delete_face(client, frozen, identity, face_ids(db, identity)[0]).status_code == 204
    assert len(face_ids(db, identity)) == 1


# ---------------------------------------------------------------- 409


def test_409_the_last_face_of_an_identity_is_not_deleted(client, db, gallery, identity):
    first, second = face_ids(db, identity)
    assert delete_face(client, gallery, identity, first).status_code == 204

    res = delete_face(client, gallery, identity, second)

    assert res.status_code == 409, res.text
    assert res.json() == {
        "code": "LAST_FACE_OF_IDENTITY",
        "message": "An identity cannot be left without faces: add the new one first",
    }
    assert face_ids(db, identity) == [second]
    assert identity_doc(db, identity)["faceCount"] == 1
    assert state(db, gallery) == (1, 1, 2, 3)


# ---------------------------------------------------------------- 404


def test_404_deleting_the_same_face_twice(client, db, gallery, identity):
    first, second = face_ids(db, identity)
    assert delete_face(client, gallery, identity, first).status_code == 204

    assert_not_found(delete_face(client, gallery, identity, first), "Face")
    assert face_ids(db, identity) == [second]
    assert identity_doc(db, identity)["faceCount"] == 1


def test_404_a_face_of_another_identity_of_the_same_gallery(
    client, db, gallery, identity, neighbour
):
    foreign = face_ids(db, neighbour)[0]

    assert_not_found(delete_face(client, gallery, identity, foreign), "Face")
    assert len(face_ids(db, neighbour)) == 2
    assert identity_doc(db, identity)["faceCount"] == 2


@pytest.mark.parametrize("face_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_face_that_does_not_or_cannot_exist(client, db, gallery, identity, face_id):
    assert_not_found(delete_face(client, gallery, identity, face_id), "Face")
    assert identity_doc(db, identity)["faceCount"] == 2


@pytest.mark.parametrize("identity_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_an_identity_that_does_not_or_cannot_exist(client, db, gallery, identity, identity_id):
    face = face_ids(db, identity)[0]

    assert_not_found(delete_face(client, gallery, identity_id, face), "Identity")
    assert len(face_ids(db, identity)) == 2


def test_404_the_identity_belongs_to_another_gallery(client, db, identity):
    other = seed_gallery(db, ACCOUNT_A, name="Otra")
    face = face_ids(db, identity)[0]

    assert_not_found(delete_face(client, other, identity, face), "Identity")
    assert len(face_ids(db, identity)) == 2


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, db, gallery, identity, role):
    face = face_ids(db, identity)[0]

    res = delete_face(client, gallery, identity, face, gateway_headers(ACCOUNT_B, role=role))

    assert_not_found(res, "Gallery")
    assert len(face_ids(db, identity)) == 2
    assert delete_face(client, gallery, identity, face).status_code == 204
