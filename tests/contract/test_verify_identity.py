"""verifyAgainstIdentity — POST …/identities/{identityId}/verify (F8, #40).

Toda respuesta pasa por el arnés (`tests/support/recognition`).

EL COMPARADOR se prueba con códigos sembrados a una distancia exacta del de la
imagen: la frontera `d_max` en los tres modelos ---que es además lo que prueba
que se infiere con el modelo DE LA GALERÍA: con otro, el código de la imagen
sería otro y las distancias no serían las sembradas--- y la menor distancia
sobre varios rostros. Dos pruebas con fotografías reales (Koch contra Koch y
contra Williams) atan el conjunto a una inferencia de verdad.

LA CADENA: la galería es de la cuenta y la identidad, de la galería. Un eslabón
roto, una marca de borrado o un identificador imposible es `404`, el mismo que
el de lo que no existe, también para `ADMIN`.

EL `403` DE LA CONGELADA VA ANTES DE INFERIR: se prueba con bytes que no son una
imagen, que darían `400 INVALID_IMAGE` si se llegase a decodificar.
"""

import pytest
from bson import ObjectId

from src.inference import lssc
from tests.contract.test_inference import (
    DEFAULT_THRESHOLD,
    KOCH_2014,
    KOCH_2023,
    MODELS,
    WILLIAMS,
    blank,
    image_part,
)
from tests.support.identities import (
    assert_bad_request,
    assert_error,
    assert_not_found,
    extracted_code,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.recognition import (
    at_distance,
    mark_deleting,
    probe_code,
    seed_identity,
    verify,
)
from tests.support.seed import seed_gallery

FIELDS = {"isTheSame", "confidence", "thresholdUsed", "model"}


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A, model="xs")


@pytest.fixture
def koch(client, db, gallery):
    """Una identidad enrolada con el retrato de Koch de 2014."""
    return seed_identity(db, gallery, [extracted_code(client, KOCH_2014, "xs")])


# ---------------------------------------------------------------- 200


def test_200_the_same_person_matches(client, gallery, koch):
    res = verify(client, gallery, koch, image=KOCH_2023)

    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == FIELDS
    assert body["isTheSame"] is True
    assert body["confidence"] >= body["thresholdUsed"]
    assert body["thresholdUsed"] == pytest.approx(DEFAULT_THRESHOLD["xs"])
    assert body["model"] == "xs-v1"


def test_200_another_person_does_not_match(client, gallery, koch):
    body = verify(client, gallery, koch, image=WILLIAMS).json()

    assert body["isTheSame"] is False
    assert body["confidence"] < body["thresholdUsed"]


@pytest.mark.parametrize("model", MODELS)
def test_200_at_d_max_it_matches_and_one_more_does_not(client, db, model):
    gallery = seed_gallery(db, ACCOUNT_A, model=model)
    probe = probe_code(client, model)
    at_d_max = seed_identity(db, gallery, [at_distance(probe, lssc.D_MAX[model])])
    beyond = seed_identity(db, gallery, [at_distance(probe, lssc.D_MAX[model] + 1)])

    inside, outside = (
        verify(client, gallery, at_d_max).json(),
        verify(client, gallery, beyond).json(),
    )

    assert inside == {
        "isTheSame": True,
        "confidence": lssc.default_threshold(model),
        "thresholdUsed": lssc.default_threshold(model),
        "model": f"{model}-v1",
    }
    assert outside["isTheSame"] is False
    assert outside["confidence"] == lssc.confidence(lssc.D_MAX[model] + 1)


@pytest.mark.parametrize("order", ["cerca primero", "cerca después"])
def test_200_the_closest_face_of_the_identity_counts(client, db, gallery, order):
    probe = probe_code(client, "xs")
    codes = [at_distance(probe, 100), at_distance(probe, 900)]
    if order == "cerca después":
        codes.reverse()
    identity = seed_identity(db, gallery, codes)

    body = verify(client, gallery, identity).json()

    assert (body["isTheSame"], body["confidence"]) == (True, lssc.confidence(100))


def test_200_the_requested_threshold_decides(client, gallery, koch):
    genuine = verify(client, gallery, koch, data={"threshold": "1"}).json()
    impostor = verify(client, gallery, koch, image=WILLIAMS, data={"threshold": "0"}).json()

    assert (genuine["isTheSame"], genuine["thresholdUsed"]) == (False, 1)
    assert (impostor["isTheSame"], impostor["thresholdUsed"]) == (True, 0)


def test_200_only_the_faces_of_that_identity_count(client, db, gallery):
    probe = probe_code(client, "xs")
    seed_identity(db, gallery, [probe])
    far = seed_identity(db, gallery, [at_distance(probe, 900)])

    assert verify(client, gallery, far).json()["confidence"] == lssc.confidence(900)


# ---------------------------------------------------------------- 400


def test_400_a_part_the_contract_does_not_declare(client, gallery, koch):
    assert verify(client, gallery, koch).status_code == 200

    assert_bad_request(verify(client, gallery, koch, data={"fullName": "Ana"}))


def test_400_a_file_part_the_contract_does_not_declare(client, gallery, koch):
    files = {"image": image_part(KOCH_2023), "evil": image_part(KOCH_2023)}

    assert_bad_request(verify(client, gallery, koch, files=files))


@pytest.mark.parametrize("part", ["embedding", "embeddingToken"])
def test_400_vector_input_is_not_supported_yet(client, gallery, koch, part):
    res = verify(client, gallery, koch, data={part: "AAAA"})

    assert_error(res, 400, "EMBEDDING_INPUT_NOT_SUPPORTED")
    assert res.json()["details"] == {"parts": [part]}


def test_400_liveness_is_not_supported_yet(client, gallery, koch):
    res = verify(client, gallery, koch, data={"livenessToken": "a-token"})

    assert_error(res, 400, "LIVENESS_NOT_SUPPORTED")
    assert res.json()["details"] == {"parts": ["livenessToken"]}


def test_400_without_image_nor_vector(client, gallery, koch):
    assert_error(verify(client, gallery, koch, image=None), 400, "BAD_REQUEST")


@pytest.mark.parametrize("threshold", ["-0.1", "1.5", "high"])
def test_400_threshold_out_of_range(client, gallery, koch, threshold):
    assert_error(verify(client, gallery, koch, data={"threshold": threshold}), 400, "BAD_REQUEST")


@pytest.mark.parametrize(
    "image,code",
    [
        (b"not an image", "INVALID_IMAGE"),
        (blank(4097, 1), "IMAGE_TOO_LARGE"),
        (blank(), "NO_FACE_DETECTED"),
    ],
    ids=["no es una imagen", "demasiado grande", "sin rostro"],
)
def test_400_an_unusable_image(client, gallery, koch, image, code):
    assert_error(verify(client, gallery, koch, image=image), 400, code)


# ---------------------------------------------------------------- 403


def test_403_a_frozen_gallery_before_inferring(client, db):
    frozen = seed_gallery(
        db, ACCOUNT_A, model="xs", status="frozen", frozenReason="MODEL_NOT_IN_PLAN"
    )
    identity = seed_identity(db, frozen, [b"\x00" * 192])

    res = verify(client, frozen, identity, image=b"not an image")

    assert_error(res, 403, "GALLERY_FROZEN_MODEL_NOT_IN_PLAN")


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, gallery, koch, role):
    res = verify(client, gallery, koch, headers=gateway_headers(ACCOUNT_B, role=role))

    assert_not_found(res, "Gallery")


@pytest.mark.parametrize("gallery_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_gallery_that_does_not_or_cannot_exist(client, koch, gallery_id):
    assert_not_found(verify(client, gallery_id, koch), "Gallery")


def test_404_a_gallery_being_deleted(client, db, gallery, koch):
    mark_deleting(db.galleries, gallery)

    assert_not_found(verify(client, gallery, koch), "Gallery")


def test_404_an_identity_of_another_gallery_of_the_same_account(client, db, gallery):
    other = seed_gallery(db, ACCOUNT_A, model="xs", name="Otra")
    foreign = seed_identity(db, other, [probe_code(client, "xs")])

    assert_not_found(verify(client, gallery, foreign), "Identity")


@pytest.mark.parametrize("identity_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_an_identity_that_does_not_or_cannot_exist(client, gallery, identity_id):
    assert_not_found(verify(client, gallery, identity_id), "Identity")


def test_404_an_identity_being_deleted(client, db, gallery, koch):
    mark_deleting(db.identities, koch)

    assert_not_found(verify(client, gallery, koch), "Identity")


def test_404_an_identity_without_faces(client, db, gallery):
    # El residuo de un `createIdentity` caído entre insertar la identidad y su
    # rostro: su cliente recibió un error y para él no existe (contrato).
    orphan = seed_identity(db, gallery, [])

    assert_not_found(verify(client, gallery, orphan, image=KOCH_2023), "Identity")
