"""searchGallery — POST …/galleries/{galleryId}/search (F8, #40).

Toda respuesta pasa por el arnés (`tests/support/recognition`).

LA RESPUESTA ES CERRADA y solo declara `identity` y `confidence`: ni
`thresholdUsed` ni `model`. `identity` siempre está, `null` sin coincidencia, y
`confidence` solo sale con ella (contrato).

EL COMPARADOR se prueba con códigos sembrados a una distancia exacta del de la
imagen: la frontera `d_max` en los tres modelos, el más cercano, el empate por
menor `_id` de rostro (con los `_id` sembrados en orden inverso al de inserción,
para que no lo decida el orden natural) y el rostro de una identidad marcada,
que da «sin coincidencia» aunque haya otra identidad por encima del umbral.
"""

import pytest
from bson import ObjectId

from src.inference import lssc
from tests.contract.test_inference import (
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
    fetch,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.recognition import (
    at_distance,
    mark_deleting,
    probe_code,
    search,
    seed_identity,
)
from tests.support.seed import seed_gallery

NO_MATCH = {"identity": None}


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A, model="xs")


@pytest.fixture
def people(client, db, gallery):
    """Koch (retrato de 2014) y Williams, enroladas en la galería."""
    return {
        "koch": seed_identity(
            db, gallery, [extracted_code(client, KOCH_2014, "xs")], fullName="Christina Koch"
        ),
        "williams": seed_identity(
            db, gallery, [extracted_code(client, WILLIAMS, "xs")], fullName="Sunita Williams"
        ),
    }


# ---------------------------------------------------------------- 200


def test_200_finds_the_person_and_returns_the_identity(client, gallery, people):
    res = search(client, gallery, image=KOCH_2023)

    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"identity", "confidence"}
    assert body["identity"] == fetch(client, gallery, people["koch"]).json()
    assert body["confidence"] >= lssc.default_threshold("xs")


def test_200_no_match_is_a_null_identity_without_confidence(client, db, gallery):
    seed_identity(db, gallery, [extracted_code(client, WILLIAMS, "xs")])

    res = search(client, gallery, image=KOCH_2023)

    assert res.status_code == 200, res.text
    assert res.json() == NO_MATCH


def test_200_an_empty_gallery_has_no_match(client, gallery):
    assert search(client, gallery).json() == NO_MATCH


@pytest.mark.parametrize("model", MODELS)
def test_200_at_d_max_it_matches_and_one_more_does_not(client, db, model):
    probe = probe_code(client, model)
    inside = seed_gallery(db, ACCOUNT_A, model=model, name="Dentro")
    outside = seed_gallery(db, ACCOUNT_A, model=model, name="Fuera")
    match = seed_identity(db, inside, [at_distance(probe, lssc.D_MAX[model])])
    seed_identity(db, outside, [at_distance(probe, lssc.D_MAX[model] + 1)])

    found = search(client, inside).json()

    assert (found["identity"]["id"], found["confidence"]) == (match, lssc.default_threshold(model))
    assert search(client, outside).json() == NO_MATCH


def test_200_the_closest_face_wins(client, db, gallery):
    probe = probe_code(client, "xs")
    seed_identity(db, gallery, [at_distance(probe, 100)])
    closest = seed_identity(db, gallery, [at_distance(probe, 400), at_distance(probe, 50)])
    seed_identity(db, gallery, [at_distance(probe, 200)])

    body = search(client, gallery).json()

    assert (body["identity"]["id"], body["confidence"]) == (closest, lssc.confidence(50))


@pytest.mark.parametrize("winner", [0, 1])
def test_200_a_tie_goes_to_the_face_with_the_smallest_id(client, db, gallery, winner):
    # Se inserta primero el de MAYOR `_id`: si decidiera el orden natural de la
    # colección, ganaría el otro.
    probe = probe_code(client, "xs")
    code = at_distance(probe, 50)
    small, large = ObjectId("6700000000000000000000a1"), ObjectId("6700000000000000000000a2")
    first_ids, second_ids = ([large], [small]) if winner == 1 else ([small], [large])
    first = seed_identity(db, gallery, [code], face_ids=first_ids)
    second = seed_identity(db, gallery, [code], face_ids=second_ids)

    body = search(client, gallery).json()

    assert body["identity"]["id"] == (second if winner == 1 else first)


def test_200_the_faces_of_another_gallery_do_not_count(client, db, gallery):
    other = seed_gallery(db, ACCOUNT_A, model="xs", name="Otra")
    seed_identity(db, other, [probe_code(client, "xs")])

    assert search(client, gallery).json() == NO_MATCH


def test_200_the_requested_threshold_decides(client, db, gallery):
    koch = seed_identity(db, gallery, [extracted_code(client, KOCH_2014, "xs")])

    genuine = search(client, gallery, image=KOCH_2023, data={"threshold": "1"}).json()
    impostor = search(client, gallery, image=WILLIAMS, data={"threshold": "0"}).json()

    assert genuine == NO_MATCH
    # Ni `thresholdUsed` ni `model`: la respuesta es cerrada y no los declara.
    assert set(impostor) == {"identity", "confidence"}
    assert impostor["identity"]["id"] == koch


@pytest.mark.parametrize("mark", [True, False], ids=["marcada", "control sin marca"])
def test_200_the_best_face_of_an_identity_being_deleted_is_no_match(client, db, gallery, mark):
    # Sin pasar al siguiente candidato, aunque esté por encima del umbral: los
    # rostros de una identidad marcada son residuo de un borrado en curso o
    # caído, y responder sin coincidencia nunca devuelve una identidad que no
    # sea la del mejor rostro.
    probe = probe_code(client, "xs")
    best = seed_identity(db, gallery, [at_distance(probe, 10)])
    seed_identity(db, gallery, [at_distance(probe, 20)])
    if mark:
        mark_deleting(db.identities, best)

    body = search(client, gallery).json()

    if mark:
        assert body == NO_MATCH
    else:
        assert body["identity"]["id"] == best


def test_200_the_best_face_of_an_identity_already_gone_is_no_match(client, db, gallery):
    probe = probe_code(client, "xs")
    gone = seed_identity(db, gallery, [at_distance(probe, 10)])
    seed_identity(db, gallery, [at_distance(probe, 20)])
    db.identities.delete_one({"_id": ObjectId(gone)})

    assert search(client, gallery).json() == NO_MATCH


# ---------------------------------------------------------------- 400


def test_400_a_part_the_contract_does_not_declare(client, gallery):
    assert search(client, gallery).status_code == 200

    assert_bad_request(search(client, gallery, data={"fullName": "Ana"}))


def test_400_liveness_token_is_not_a_part_of_search(client, gallery):
    assert_bad_request(search(client, gallery, data={"livenessToken": "a-token"}))


def test_400_a_file_part_the_contract_does_not_declare(client, gallery):
    files = {"image": image_part(KOCH_2023), "evil": image_part(KOCH_2023)}

    assert_bad_request(search(client, gallery, files=files))


@pytest.mark.parametrize("part", ["embedding", "embeddingToken"])
def test_400_vector_input_is_not_supported_yet(client, gallery, part):
    res = search(client, gallery, data={part: "AAAA"})

    assert_error(res, 400, "EMBEDDING_INPUT_NOT_SUPPORTED")
    assert res.json()["details"] == {"parts": [part]}


def test_400_without_image_nor_vector(client, gallery):
    assert_error(search(client, gallery, image=None), 400, "BAD_REQUEST")


@pytest.mark.parametrize("threshold", ["-0.1", "1.5", "high"])
def test_400_threshold_out_of_range(client, gallery, threshold):
    assert_error(search(client, gallery, data={"threshold": threshold}), 400, "BAD_REQUEST")


@pytest.mark.parametrize(
    "image,code",
    [
        (b"not an image", "INVALID_IMAGE"),
        (blank(4097, 1), "IMAGE_TOO_LARGE"),
        (blank(), "NO_FACE_DETECTED"),
    ],
    ids=["no es una imagen", "demasiado grande", "sin rostro"],
)
def test_400_an_unusable_image(client, gallery, image, code):
    assert_error(search(client, gallery, image=image), 400, code)


# ---------------------------------------------------------------- 403


def test_403_a_frozen_gallery_before_inferring(client, db):
    frozen = seed_gallery(
        db, ACCOUNT_A, model="xs", status="frozen", frozenReason="MODEL_NOT_IN_PLAN"
    )

    res = search(client, frozen, image=b"not an image")

    assert_error(res, 403, "GALLERY_FROZEN_MODEL_NOT_IN_PLAN")


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account(client, gallery, people, role):
    res = search(client, gallery, headers=gateway_headers(ACCOUNT_B, role=role))

    assert_not_found(res, "Gallery")


@pytest.mark.parametrize("gallery_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_gallery_that_does_not_or_cannot_exist(client, gallery_id):
    assert_not_found(search(client, gallery_id), "Gallery")


def test_404_a_gallery_being_deleted(client, db, gallery, people):
    mark_deleting(db.galleries, gallery)

    assert_not_found(search(client, gallery), "Gallery")
