"""updateGallery — PATCH /v1/fr/commercial/galleries/{galleryId} (F3, #35).

Toda respuesta pasa por el arnés (`tests/support/galleries.checked`).

CADA RECHAZO COMPRUEBA QUE NO SE ESCRIBIÓ NADA: el documento guardado es,
campo a campo, el de antes. Un `400` o un `404` que hubiera escrito sería un
rechazo solo en la respuesta.

CADA 404 VA EMPAREJADO con el `200` del propietario, como en `getGallery`.
"""

from datetime import datetime

import pytest
from bson import ObjectId

from tests.support.galleries import (
    assert_bad_request,
    assert_name_conflict,
    assert_not_found,
    assert_owner_sees_it,
    create,
    fetch,
    update,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import seed_gallery


def doc(db, gallery_id):
    return db.galleries.find_one({"_id": ObjectId(gallery_id)})


def new_gallery(client, name="Empleados", **extra):
    return create(client, {"name": name, **extra}).json()


# ---------------------------------------------------------------- 200


def test_200_renaming_changes_the_name_and_sets_updatedAt(client, db):
    before = new_gallery(client, description="plantilla", model="s")

    res = update(client, before["id"], {"name": "Plantilla"})

    assert res.status_code == 200, res.text
    after = res.json()
    assert after["name"] == "Plantilla"
    assert datetime.fromisoformat(after["updatedAt"]) >= datetime.fromisoformat(before["createdAt"])
    unchanged = {k: v for k, v in after.items() if k not in ("name", "updatedAt")}
    assert unchanged == {k: v for k, v in before.items() if k != "name"}
    assert fetch(client, before["id"]).json() == after


def test_200_only_the_description_leaves_the_name_alone(client, db):
    gallery = new_gallery(client)

    after = update(client, gallery["id"], {"description": "nueva"}).json()

    assert (after["name"], after["description"]) == ("Empleados", "nueva")


def test_200_renaming_with_its_own_name_is_not_a_conflict(client, db):
    gallery = new_gallery(client)

    res = update(client, gallery["id"], {"name": "Empleados"})

    assert res.status_code == 200, res.text


def test_200_an_empty_body_writes_nothing_and_does_not_touch_updatedAt(client, db):
    gallery = new_gallery(client)
    stored = doc(db, gallery["id"])

    res = update(client, gallery["id"], {})

    assert res.status_code == 200, res.text
    assert res.json() == gallery
    assert "updatedAt" not in res.json()
    assert doc(db, gallery["id"]) == stored


def test_200_a_frozen_gallery_can_be_edited_and_stays_frozen(client, db):
    # updateGallery no declara ningún 403 de dominio: congelar bloquea enrolar,
    # verificar y buscar, no los metadatos.
    gallery_id = seed_gallery(db, ACCOUNT_A, status="frozen", frozenReason="MODEL_NOT_IN_PLAN")

    res = update(client, gallery_id, {"name": "Archivo"})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "frozen"
    assert res.json()["frozenReason"] == "MODEL_NOT_IN_PLAN"


def test_200_the_name_of_another_accounts_gallery_is_free_here(client, db):
    create(client, {"name": "Compartido"}, gateway_headers(ACCOUNT_B))
    gallery = new_gallery(client)

    assert update(client, gallery["id"], {"name": "Compartido"}).status_code == 200


def test_200_an_empty_name_is_accepted_because_the_contract_allows_it(client, db):
    gallery = new_gallery(client)

    assert update(client, gallery["id"], {"name": ""}).json()["name"] == ""


# -------------------------------------------- las cotas, en los dos lados

BOUNDS = {
    "name 120": ({"name": "n" * 120}, 200),
    "name 121": ({"name": "n" * 121}, 400),
    "description 500": ({"description": "d" * 500}, 200),
    "description 501": ({"description": "d" * 501}, 400),
}


@pytest.mark.parametrize("body,status", BOUNDS.values(), ids=BOUNDS.keys())
def test_each_length_bound_on_both_sides(client, db, body, status):
    gallery = new_gallery(client)
    stored = doc(db, gallery["id"])

    res = update(client, gallery["id"], body)

    assert res.status_code == status, res.text
    if status == 400:
        assert doc(db, gallery["id"]) == stored
    else:
        assert {k: res.json()[k] for k in body} == body


# ---------------------------------------------------------------- 400

INVALID = {
    "model, que no se modifica": {"model": "base"},
    "status, que gobierna el sistema": {"status": "frozen"},
    "frozenReason": {"frozenReason": "MODEL_NOT_IN_PLAN"},
    "un campo de más (accountId)": {"name": "x", "accountId": ACCOUNT_B},
    "name null": {"name": None},
    "description null": {"description": None},
    "name que no es cadena": {"name": 123},
}


def test_the_valid_body_that_every_invalid_one_derives_from_is_a_200(client, db):
    gallery = new_gallery(client)

    assert update(client, gallery["id"], {"name": "x"}).status_code == 200


@pytest.mark.parametrize("body", INVALID.values(), ids=INVALID.keys())
def test_400_an_invalid_body_is_rejected_and_nothing_is_written(client, db, body):
    gallery = new_gallery(client)
    stored = doc(db, gallery["id"])

    assert_bad_request(update(client, gallery["id"], body))
    assert doc(db, gallery["id"]) == stored


def test_401_comes_before_400(client, db):
    gallery = new_gallery(client)

    res = client.patch(f"/v1/fr/commercial/galleries/{gallery['id']}", json={"model": "xl"})

    assert res.status_code == 401, res.text


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_another_account_cannot_edit_it_and_nothing_is_written(client, db, role):
    gallery_id = seed_gallery(db, ACCOUNT_A)
    stored = doc(db, gallery_id)

    assert_not_found(
        update(client, gallery_id, {"name": "mía"}, gateway_headers(ACCOUNT_B, role=role))
    )
    assert doc(db, gallery_id) == stored
    assert_owner_sees_it(client, gallery_id)


@pytest.mark.parametrize("gallery_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_gallery_that_does_not_or_cannot_exist(client, db, gallery_id):
    existing = seed_gallery(db, ACCOUNT_A)

    assert_not_found(update(client, gallery_id, {"name": "x"}))
    assert_owner_sees_it(client, existing)


def test_404_an_empty_body_on_another_accounts_gallery_is_still_a_404(client, db):
    # El cuerpo vacío no escribe, pero lee con la cuenta en el filtro.
    gallery_id = seed_gallery(db, ACCOUNT_A)

    assert_not_found(update(client, gallery_id, {}, gateway_headers(ACCOUNT_B)))
    assert_owner_sees_it(client, gallery_id)


# ---------------------------------------------------------------- 409


def test_409_renaming_to_a_name_that_exists_in_the_account(client, db):
    # El DuplicateKeyError de `find_one_and_update` no lleva `index`, solo
    # `keyPattern` (medido en #35): la traducción sale de ahí.
    new_gallery(client, "Empleados")
    other = new_gallery(client, "Visitas")
    stored = doc(db, other["id"])

    assert_name_conflict(update(client, other["id"], {"name": "Empleados", "description": "x"}))
    assert doc(db, other["id"]) == stored
