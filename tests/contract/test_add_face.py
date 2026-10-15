"""addFace — POST …/galleries/{galleryId}/identities/{identityId}/faces (F4 y F6).

Toda respuesta pasa por el arnés (`tests/support/identities.checked`).

LA CADENA: la galería es de la cuenta y la identidad, de la galería. Un eslabón
roto es `404`, y el mensaje nombra el primero que se rompe. Una identidad de
OTRA galería de la misma cuenta es una identidad que no existe aquí.

CADA RECHAZO COMPRUEBA QUE NO SE ESCRIBIÓ NADA, contadores incluidos.

La carrera con `deleteIdentity` y con `deleteGallery`, en `test_races.py`.
"""

import pytest
from bson import ObjectId

from src.inference.lssc import CODE_BYTES
from tests.contract.test_inference import KOCH_2023, blank, image_part
from tests.support.identities import (
    FACE_FIELDS,
    add_face,
    assert_bad_request,
    assert_error,
    assert_not_found,
    extracted_code,
    faces_of,
    identity_doc,
    state,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import seed_gallery, seed_identity_with_faces


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A, model="xs", identityCount=1, faceCount=1)


@pytest.fixture
def identity(db, gallery):
    return seed_identity_with_faces(db, gallery, faces=1)


# ---------------------------------------------------------------- 201


def test_201_adds_a_face_and_counts_it(client, db, gallery, identity):
    res = add_face(client, gallery, identity)

    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body) == FACE_FIELDS
    assert (body["identityId"], body["galleryId"]) == (identity, gallery)
    assert (body["isBinarized"], body["source"]) == (True, "image")
    assert identity_doc(db, identity)["faceCount"] == 2
    assert [str(f["_id"]) for f in faces_of(db, identity)][-1] == body["id"]
    assert state(db, gallery) == (1, 2, 1, 2)


def test_201_the_face_is_the_lssc_code_of_the_gallery_model(client, db):
    gallery = seed_gallery(db, ACCOUNT_A, model="s")
    identity = seed_identity_with_faces(db, gallery, faces=1)

    body = add_face(client, gallery, identity, image=KOCH_2023).json()

    stored = next(f for f in faces_of(db, identity) if str(f["_id"]) == body["id"])
    assert len(stored["embedding"]) == CODE_BYTES
    assert stored["embedding"] == extracted_code(client, KOCH_2023, "s")


def test_201_there_is_no_limit_of_faces_per_identity(client, db, gallery, identity):
    for _ in range(3):
        assert add_face(client, gallery, identity).status_code == 201

    assert identity_doc(db, identity)["faceCount"] == 4


# ---------------------------------------------------------------- 400


def test_400_a_part_the_contract_does_not_declare(client, db, gallery, identity):
    assert add_face(client, gallery, identity).status_code == 201

    assert_bad_request(add_face(client, gallery, identity, data={"fullName": "Ana"}))
    assert state(db, gallery) == (1, 2, 1, 2)


def test_400_a_file_part_the_contract_does_not_declare(client, db, gallery, identity):
    files = {"image": image_part(KOCH_2023), "evil": image_part(KOCH_2023)}

    assert_bad_request(add_face(client, gallery, identity, files=files))
    assert state(db, gallery) == (1, 1, 1, 1)


@pytest.mark.parametrize("part", ["embedding", "embeddingToken"])
def test_400_vector_input_is_not_supported_yet(client, db, gallery, identity, part):
    res = add_face(client, gallery, identity, data={part: "AAAA"})

    assert_error(res, 400, "EMBEDDING_INPUT_NOT_SUPPORTED")
    assert state(db, gallery) == (1, 1, 1, 1)


def test_400_without_image_nor_vector(client, db, gallery, identity):
    assert_error(add_face(client, gallery, identity, image=None), 400, "BAD_REQUEST")
    assert state(db, gallery) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    "image,code",
    [(b"not an image", "INVALID_IMAGE"), (blank(), "NO_FACE_DETECTED")],
    ids=["no es una imagen", "sin rostro"],
)
def test_400_an_unusable_image_writes_nothing(client, db, gallery, identity, image, code):
    assert_error(add_face(client, gallery, identity, image=image), 400, code)
    assert state(db, gallery) == (1, 1, 1, 1)
    assert identity_doc(db, identity)["faceCount"] == 1


# ---------------------------------------------------------------- 403


def test_403_a_frozen_gallery_does_not_enroll(client, db):
    frozen = seed_gallery(
        db, ACCOUNT_A, status="frozen", frozenReason="MODEL_NOT_IN_PLAN", faceCount=1
    )
    identity = seed_identity_with_faces(db, frozen, faces=1)

    assert_error(add_face(client, frozen, identity), 403, "GALLERY_FROZEN_MODEL_NOT_IN_PLAN")
    assert len(faces_of(db, identity)) == 1
    assert identity_doc(db, identity)["faceCount"] == 1


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, db, gallery, identity, role):
    res = add_face(client, gallery, identity, headers=gateway_headers(ACCOUNT_B, role=role))

    assert_not_found(res, "Gallery")
    assert state(db, gallery) == (1, 1, 1, 1)


def test_404_an_identity_of_another_gallery_of_the_same_account(client, db, gallery):
    other = seed_gallery(db, ACCOUNT_A, name="Otra")
    foreign = seed_identity_with_faces(db, other, faces=1)

    assert_not_found(add_face(client, gallery, foreign), "Identity")
    assert len(faces_of(db, foreign)) == 1
    assert state(db, gallery) == (0, 0, 1, 1)


@pytest.mark.parametrize("identity_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_an_identity_that_does_not_or_cannot_exist(client, db, gallery, identity_id):
    assert_not_found(add_face(client, gallery, identity_id), "Identity")
    assert db.faces.count_documents({}) == 0


def test_404_a_gallery_that_cannot_exist(client, db, identity):
    assert_not_found(add_face(client, "no-es-un-objectid", identity), "Gallery")
