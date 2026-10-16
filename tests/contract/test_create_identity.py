"""createIdentity — POST /v1/fr/commercial/galleries/{galleryId}/identities (F4 y F6).

Toda respuesta pasa por el arnés (`tests/support/identities.checked`).

CADA RECHAZO COMPRUEBA QUE NO SE ESCRIBIÓ NADA: ni identidad, ni rostro, ni
contadores. La inferencia va antes de cualquier escritura, así que un `400` de
imagen no deja una identidad sin rostro.

EL ROSTRO GUARDADO (F6, #38) es el código LSSC de 192 B del modelo DE LA
GALERÍA, y se comprueba contra lo que da `extractEmbedding` para la misma
imagen y el mismo modelo: los mismos bytes. Eso ata el almacenamiento al mismo
camino ---rostro de mayor confianza, alineación, modelo, cortes--- que ya prueba
`test_inference.py`.

EL CIERRE DEL CUERPO MULTIPART se prueba con peticiones, emparejadas con la
legítima; la comparación estática lo deja fuera con su motivo (`test_shape.py`).

La carrera con `deleteGallery`, en `test_races.py`.
"""

import pytest
from bson import ObjectId

from src.inference.lssc import CODE_BYTES
from tests.contract.test_inference import KOCH_2023, WILLIAMS, blank, image_part
from tests.support.identities import (
    IDENTITY_REQUIRED,
    assert_bad_request,
    assert_error,
    assert_not_found,
    enroll,
    extracted_code,
    faces_of,
    identity_doc,
    state,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import seed_gallery


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A, model="xs", identityCount=0, faceCount=0)


# ---------------------------------------------------------------- 201


def test_201_creates_the_identity_with_its_first_face(client, db, gallery):
    res = enroll(client, gallery, {"fullName": "Ana"})

    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body) == IDENTITY_REQUIRED
    assert (body["galleryId"], body["fullName"], body["faceCount"]) == (gallery, "Ana", 1)

    stored = identity_doc(db, body["id"])
    assert stored["faceCount"] == 1
    assert "externalId" not in stored
    [face] = faces_of(db, body["id"])
    assert face["galleryId"] == ObjectId(gallery)
    assert (face["isBinarized"], face["source"]) == (True, "image")
    assert state(db, gallery) == (1, 1, 1, 1)


def test_201_with_externalId_it_is_stored_and_returned(client, db, gallery):
    body = enroll(client, gallery, {"fullName": "Ana", "externalId": "E-1"}).json()

    assert body["externalId"] == "E-1"
    assert identity_doc(db, body["id"])["externalId"] == "E-1"


def test_201_an_empty_fullName_is_a_name(client, db, gallery):
    # El contrato no declara `minLength` en `fullName`; sí en `externalId`.
    res = enroll(client, gallery, {"fullName": ""})

    assert res.status_code == 201, res.text
    assert res.json()["fullName"] == ""


def test_201_two_identities_without_externalId_coexist(client, db, gallery):
    assert enroll(client, gallery).status_code == 201
    assert enroll(client, gallery).status_code == 201
    assert state(db, gallery) == (2, 2, 2, 2)


def test_201_the_same_externalId_in_another_gallery(client, db, gallery):
    other = seed_gallery(db, ACCOUNT_A, name="Otra", model="xs")
    assert enroll(client, gallery, {"fullName": "Ana", "externalId": "E-1"}).status_code == 201

    res = enroll(client, other, {"fullName": "Ana", "externalId": "E-1"})

    assert res.status_code == 201, res.text


# ------------------------------------------------------- F6: el rostro guardado


@pytest.mark.parametrize("model", ["xs", "s", "base"])
def test_201_the_face_is_the_192_byte_lssc_code_of_the_gallery_model(client, db, model):
    gallery = seed_gallery(db, ACCOUNT_A, model=model)

    body = enroll(client, gallery, image=KOCH_2023).json()

    [face] = faces_of(db, body["id"])
    assert len(face["embedding"]) == CODE_BYTES == 192
    assert face["embedding"] == extracted_code(client, KOCH_2023, model)


def test_201_the_face_of_another_model_is_another_code(client, db):
    # Control del anterior: si se ignorara el modelo de la galería, `xs` y `s`
    # guardarían lo mismo.
    xs = seed_gallery(db, ACCOUNT_A, name="xs", model="xs")
    s = seed_gallery(db, ACCOUNT_A, name="s", model="s")

    codes = [faces_of(db, enroll(client, g).json()["id"])[0]["embedding"] for g in (xs, s)]

    assert codes[0] != codes[1]


# ---------------------------------------------------------------- 409


def test_409_a_repeated_externalId_in_the_gallery_writes_nothing(client, db, gallery):
    enroll(client, gallery, {"fullName": "Ana", "externalId": "E-1"})

    res = enroll(client, gallery, {"fullName": "Eva", "externalId": "E-1"})

    assert res.status_code == 409, res.text
    assert res.json() == {
        "code": "EXTERNAL_ID_ALREADY_EXISTS",
        "message": "An identity with that externalId already exists in the gallery",
    }
    assert state(db, gallery) == (1, 1, 1, 1)


# ---------------------------------------------------------------- 400


def test_400_an_empty_externalId_is_rejected_not_taken_as_absent(client, db, gallery):
    assert_bad_request(enroll(client, gallery, {"fullName": "Ana", "externalId": ""}))
    assert state(db, gallery) == (0, 0, 0, 0)


def test_400_without_fullName(client, db, gallery):
    assert_bad_request(enroll(client, gallery, {"externalId": "E-1"}))
    assert state(db, gallery) == (0, 0, 0, 0)


@pytest.mark.parametrize("part", ["accountId", "galleryId"])
def test_400_a_part_the_contract_does_not_declare(client, db, gallery, part):
    assert enroll(client, gallery, {"fullName": "Ana"}).status_code == 201

    assert_bad_request(enroll(client, gallery, {"fullName": "Ana", part: "x"}))
    assert state(db, gallery) == (1, 1, 1, 1)


def test_400_a_file_part_the_contract_does_not_declare(client, db, gallery):
    files = {"image": image_part(KOCH_2023), "evil": image_part(KOCH_2023)}

    assert_bad_request(enroll(client, gallery, files=files))
    assert state(db, gallery) == (0, 0, 0, 0)


@pytest.mark.parametrize("part", ["embedding", "embeddingToken"])
def test_400_vector_input_is_not_supported_yet(client, db, gallery, part):
    res = enroll(client, gallery, {"fullName": "Ana", part: "AAAA"})

    assert_error(res, 400, "EMBEDDING_INPUT_NOT_SUPPORTED")
    assert res.json()["details"] == {"parts": [part]}
    assert state(db, gallery) == (0, 0, 0, 0)


def test_400_without_image_nor_vector(client, db, gallery):
    assert_error(enroll(client, gallery, image=None), 400, "BAD_REQUEST")
    assert state(db, gallery) == (0, 0, 0, 0)


IMAGE_ERRORS = {
    "no es una imagen": (b"not an image", "INVALID_IMAGE"),
    "sin rostro": (blank(), "NO_FACE_DETECTED"),
    "más de 4096 px": (blank(4097, 8), "IMAGE_TOO_LARGE"),
}


@pytest.mark.parametrize("image,code", IMAGE_ERRORS.values(), ids=IMAGE_ERRORS.keys())
def test_400_an_unusable_image_writes_nothing(client, db, gallery, image, code):
    assert_error(enroll(client, gallery, image=image), 400, code)
    assert state(db, gallery) == (0, 0, 0, 0)


# ---------------------------------------------------------------- 403


def test_403_a_frozen_gallery_does_not_enroll(client, db):
    frozen = seed_gallery(
        db,
        ACCOUNT_A,
        model="base",
        status="frozen",
        frozenReason="MODEL_NOT_IN_PLAN",
        identityCount=0,
        faceCount=0,
    )

    res = enroll(client, frozen)

    assert res.status_code == 403, res.text
    assert res.json() == {
        "code": "GALLERY_FROZEN_MODEL_NOT_IN_PLAN",
        "message": "The gallery is frozen: its model is not available in the current plan",
    }
    assert state(db, frozen) == (0, 0, 0, 0)


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, db, gallery, role):
    res = enroll(client, gallery, headers=gateway_headers(ACCOUNT_B, role=role))

    assert_not_found(res, "Gallery")
    assert state(db, gallery) == (0, 0, 0, 0)
    assert enroll(client, gallery).status_code == 201


@pytest.mark.parametrize("gallery_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_gallery_that_does_not_or_cannot_exist(client, db, gallery_id):
    assert_not_found(enroll(client, gallery_id, image=WILLIAMS), "Gallery")
    assert db.identities.count_documents({}) == db.faces.count_documents({}) == 0
