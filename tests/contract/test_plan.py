"""El plan de la cuenta (F10, #42): un código nuevo por bloque, todo contra el contrato.

SPACE está doblado (`space`, tests/support/plan.py): cada prueba elige el plan
de la tarifa que necesita y, cuando importa, mira qué unidades se imputaron. El
cliente de verdad, con sus 503, se prueba en tests/unit/test_space.py.

Cada respuesta pasa por el arnés: estado declarado, `ErrorReport` y, en el 503,
la cabecera `Retry-After`.
"""

import pytest

from tests.contract.harness import assert_bare_error, assert_satisfies_contract
from tests.contract.test_inference import (
    DETECTION,
    KOCH_2014,
    KOCH_2023,
    VERIFICATION,
    blank,
    image_part,
)
from tests.support import identities
from tests.support.galleries import GALLERIES, ONE
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.plan import RESET_AT, business, free, pro
from tests.support.recognition import search, verify
from tests.support.seed import seed_gallery, seed_identity_with_faces

WEB = gateway_headers(channel="frontend")
API = gateway_headers(channel="api")


def checked(response):
    assert_satisfies_contract(response)
    if response.status_code >= 400 and not response.json().get("details"):
        assert_bare_error(response)
    return response


def assert_code(res, status, code):
    assert res.status_code == status, res.text
    assert res.json()["code"] == code, res.text


def detect(client, image=KOCH_2023, headers=WEB):
    return checked(client.post(DETECTION, files={"image": image_part(image)}, headers=headers))


def pair(client, data=None, image1=KOCH_2014, image2=KOCH_2023, headers=WEB):
    files = {"image1": image_part(image1), "image2": image_part(image2)}
    return checked(client.post(VERIFICATION, files=files, data=data, headers=headers))


def galleries(client, headers=WEB):
    return checked(client.get(GALLERIES, headers=headers))


def create_gallery(client, body, headers=WEB):
    return checked(client.post(GALLERIES, json=body, headers=headers))


# =================================================================== PLAN_UNAVAILABLE


@pytest.mark.parametrize("headers", [WEB, API], ids=["web", "api"])
def test_503_plan_unavailable_in_both_channels_with_retry_after(client, space, headers):
    space.down = True

    res = galleries(client, headers)

    assert_code(res, 503, "PLAN_UNAVAILABLE")
    assert res.headers["retry-after"] == "5"


def test_503_plan_unavailable_admits_nothing_by_default(client, db, space):
    space.down = True

    res = create_gallery(client, {"name": "Empleados"})

    assert_code(res, 503, "PLAN_UNAVAILABLE")
    assert db.galleries.count_documents({}) == 0


def test_503_plan_unavailable_when_space_fails_to_consume(client, space):
    """El plan se leyó, pero la imputación no se pudo hacer: no se infiere."""
    plan = space.plan

    async def plan_then_down(account_id):
        result = await plan(account_id)
        space.down = True
        return result

    space.plan = plan_then_down

    assert_code(detect(client), 503, "PLAN_UNAVAILABLE")


# =================================================================== FEATURE_NOT_IN_PLAN


def test_403_feature_not_in_plan_free_through_the_api_channel(client, space):
    space.use(free())

    res = galleries(client, API)

    assert_code(res, 403, "FEATURE_NOT_IN_PLAN")
    assert res.json()["details"] == {"feature": "apiChannel"}


def test_free_through_the_web_channel_is_served(client, space):
    space.use(free())

    assert galleries(client, WEB).status_code == 200


def test_403_feature_not_in_plan_free_cannot_search(client, db, space):
    space.use(free())
    gallery = seed_gallery(db, ACCOUNT_A, model="xs")

    res = search(client, gallery, headers=WEB)

    assert_code(res, 403, "FEATURE_NOT_IN_PLAN")
    assert res.json()["details"] == {"feature": "gallerySearch"}
    assert space.consumed == []


@pytest.mark.parametrize("plan", [free, pro], ids=["free", "pro"])
def test_403_feature_not_in_plan_threshold_needs_the_configurable_threshold(client, space, plan):
    space.use(plan())

    res = pair(client, data={"threshold": "0.7"})

    assert_code(res, 403, "FEATURE_NOT_IN_PLAN")
    assert res.json()["details"] == {"feature": "configurableThreshold"}
    assert space.consumed == []


def test_business_may_send_a_threshold(client, space):
    space.use(business())

    res = pair(client, data={"threshold": "0.7"})

    assert res.status_code == 200, res.text
    assert res.json()["thresholdUsed"] == 0.7


# =================================================================== MODEL_NOT_IN_PLAN


def test_403_model_not_in_plan_free_creating_a_base_gallery(client, db, space):
    space.use(free())

    res = create_gallery(client, {"name": "Empleados", "model": "base"})

    assert_code(res, 403, "MODEL_NOT_IN_PLAN")
    assert res.json()["details"] == {"model": "base"}
    assert db.galleries.count_documents({}) == 0


def test_403_model_not_in_plan_free_verifying_with_s(client, space):
    space.use(free())

    res = pair(client, data={"model": "s"})

    assert_code(res, 403, "MODEL_NOT_IN_PLAN")
    assert space.consumed == []


def test_free_creates_an_xs_gallery(client, space):
    space.use(free())

    assert create_gallery(client, {"name": "Empleados", "model": "xs"}).status_code == 201


# =================================================================== GALLERY_LIMIT_REACHED


def test_403_gallery_limit_reached_free_has_one(client, db, space):
    space.use(free())
    assert create_gallery(client, {"name": "Una"}).status_code == 201

    res = create_gallery(client, {"name": "Otra"})

    assert_code(res, 403, "GALLERY_LIMIT_REACHED")
    assert res.json()["details"] == {"limit": 1}
    assert db.galleries.count_documents({}) == 1


def test_the_gallery_limit_counts_only_the_galleries_of_the_account(client, db, space):
    space.use(free())
    seed_gallery(db, ACCOUNT_B)

    assert create_gallery(client, {"name": "Una"}).status_code == 201


# =================================================================== INDEXED_FACES_LIMIT_REACHED


def test_403_indexed_faces_limit_reached_counts_every_gallery_of_the_account(client, db, space):
    """El límite es por cuenta: 4 + 3 rostros en dos galerías con un límite de 7."""
    space.use(pro(), maxFaces=7)
    seed_gallery(db, ACCOUNT_A, name="Una", model="xs", faceCount=4)
    gallery = seed_gallery(db, ACCOUNT_A, name="Otra", model="xs", faceCount=3)

    res = identities.enroll(client, gallery)

    assert_code(res, 403, "INDEXED_FACES_LIMIT_REACHED")
    assert res.json()["details"] == {"limit": 7}
    assert space.consumed == []


def test_403_indexed_faces_limit_reached_also_adding_a_face(client, db, space):
    space.use(pro(), maxFaces=2)
    gallery = seed_gallery(db, ACCOUNT_A, model="xs", faceCount=2)
    identity = seed_identity_with_faces(db, gallery, faces=2)

    res = identities.add_face(client, gallery, identity)

    assert_code(res, 403, "INDEXED_FACES_LIMIT_REACHED")


def test_the_faces_of_other_accounts_do_not_count(client, db, space):
    space.use(pro(), maxFaces=7)
    seed_gallery(db, ACCOUNT_B, model="xs", faceCount=50)
    gallery = seed_gallery(db, ACCOUNT_A, model="xs", faceCount=6)

    res = identities.enroll(client, gallery)

    assert res.status_code == 201, res.text


# ============================================================ GALLERY_FROZEN_MODEL_NOT_IN_PLAN


@pytest.fixture
def frozen_on_the_fly(db, space):
    """Una galería `base`, `active` en la base, de una cuenta que ya es Free."""
    space.use(free())
    gallery = seed_gallery(db, ACCOUNT_A, model="base", status="active")
    identity = seed_identity_with_faces(db, gallery, faces=2)
    return gallery, identity


@pytest.mark.parametrize("operation", ["enroll", "add_face", "verify", "search"])
def test_403_gallery_frozen_when_the_plan_no_longer_has_its_model(
    client, space, frozen_on_the_fly, operation
):
    gallery, identity = frozen_on_the_fly
    space.use(pro())  # Pro no incluye `base`, pero sí la búsqueda.
    call = {
        "enroll": lambda: identities.enroll(client, gallery),
        "add_face": lambda: identities.add_face(client, gallery, identity),
        "verify": lambda: verify(client, gallery, identity),
        "search": lambda: search(client, gallery),
    }[operation]

    res = call()

    assert_code(res, 403, "GALLERY_FROZEN_MODEL_NOT_IN_PLAN")
    assert space.consumed == []


def test_a_gallery_frozen_on_the_fly_can_still_be_read_and_deleted_and_is_not_rewritten(
    client, db, frozen_on_the_fly
):
    gallery, identity = frozen_on_the_fly

    assert identities.listing(client, gallery).status_code == 200
    read = checked(client.get(ONE.format(gallery), headers=WEB))
    assert read.status_code == 200
    assert read.json()["status"] == "frozen"
    # Al vuelo: no se escribe.
    assert db.galleries.find_one({})["status"] == "active"
    assert "frozenReason" not in db.galleries.find_one({})
    assert identities.delete(client, gallery, identity).status_code == 204


@pytest.fixture
def base_gallery_in_pro(db, space):
    space.use(pro())
    return seed_gallery(db, ACCOUNT_A, model="base", status="active")


def test_a_base_gallery_reads_frozen_in_pro_without_the_base_model(client, base_gallery_in_pro):
    """Lo que dicen las lecturas es lo que deciden las altas: congelada."""
    one = checked(client.get(ONE.format(base_gallery_in_pro), headers=WEB)).json()
    (listed,) = galleries(client).json()["items"]

    for gallery in (one, listed):
        assert (gallery["status"], gallery["frozenReason"]) == ("frozen", "MODEL_NOT_IN_PLAN")
    renamed = checked(
        client.patch(ONE.format(base_gallery_in_pro), json={"name": "Otra"}, headers=WEB)
    )
    assert renamed.json()["status"] == "frozen"
    assert_code(
        identities.enroll(client, base_gallery_in_pro), 403, "GALLERY_FROZEN_MODEL_NOT_IN_PLAN"
    )


def test_the_same_gallery_reads_active_with_the_base_model_add_on(
    client, base_gallery_in_pro, space
):
    space.features["modelBase"] = True  # el complemento baseModel

    one = checked(client.get(ONE.format(base_gallery_in_pro), headers=WEB)).json()
    (listed,) = galleries(client).json()["items"]

    for gallery in (one, listed):
        assert gallery["status"] == "active"
        assert "frozenReason" not in gallery


# =================================================================== QUOTA_EXCEEDED


@pytest.mark.parametrize(
    ("limit", "name"),
    [
        ("capacityUnitsPerMinute", "minute"),
        ("capacityUnitsPerHour", "hour"),
        ("capacityUnitsPerMonth", "month"),
    ],
)
def test_403_quota_exceeded_says_which_limit_and_when_it_renews(client, space, limit, name):
    space.exhausted = limit

    res = detect(client)

    assert_code(res, 403, "QUOTA_EXCEEDED")
    assert res.json()["details"] == {"limit": name, "resetAt": RESET_AT}


def test_with_the_minute_exhausted_management_still_works_and_enrolling_does_not(client, db, space):
    """Las unidades solo se comprueban al consumir: una cuenta sin unidades puede
    listar y borrar identidades. Decidir el acceso con la evaluación de
    `identityManagement` del token, que comprueba unidades, lo impediría."""
    gallery = seed_gallery(db, ACCOUNT_A, model="xs")
    identity = seed_identity_with_faces(db, gallery, faces=2)
    space.exhausted = "capacityUnitsPerMinute"

    assert identities.listing(client, gallery).status_code == 200
    assert_code(identities.add_face(client, gallery, identity), 403, "QUOTA_EXCEEDED")
    assert identities.delete(client, gallery, identity).status_code == 204


# =================================================================== qué se cobra


@pytest.mark.parametrize(
    ("call", "feature", "units"),
    [
        (lambda c, db: detect(c), "faceDetection", 1.0),
        (lambda c, db: pair(c), "pairVerification", 3.0),
        (
            lambda c, db: identities.enroll(
                c, seed_gallery(db, ACCOUNT_A, model="xs", faceCount=0)
            ),
            "identityManagement",
            1.5,
        ),
    ],
    ids=["detectFaces", "verifyPair", "createIdentity"],
)
def test_each_operation_charges_its_units_to_the_account(client, db, space, call, feature, units):
    res = call(client, db)

    assert res.status_code in (200, 201), res.text
    assert space.consumed == [(ACCOUNT_A, feature, units)]


def test_search_charges_one_and_a_half_plus_five_hundredths_per_thousand_faces(client, db, space):
    gallery = seed_gallery(db, ACCOUNT_A, model="xs", faceCount=7000)
    seed_identity_with_faces(db, gallery, faces=1)

    assert search(client, gallery).status_code == 200
    ((account, feature, units),) = space.consumed
    assert (account, feature) == (ACCOUNT_A, "gallerySearch")
    assert units == pytest.approx(1.85)


def test_management_charges_nothing(client, db, space):
    gallery = seed_gallery(db, ACCOUNT_A, model="xs")

    assert identities.listing(client, gallery).status_code == 200
    assert galleries(client).status_code == 200
    assert space.consumed == []


def test_an_image_that_cannot_be_decoded_is_rejected_before_charging(client, space):
    res = detect(client, image=b"not an image at all")

    assert_code(res, 400, "INVALID_IMAGE")
    assert space.consumed == []


def test_what_fails_after_charging_stays_charged(client, space):
    """Sin deshacer: una imagen sin rostros solo se sabe infiriendo."""
    res = pair(client, image1=blank())

    assert_code(res, 400, "NO_FACE_DETECTED")
    assert space.consumed == [(ACCOUNT_A, "pairVerification", 3.0)]
