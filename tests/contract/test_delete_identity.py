"""deleteIdentity — DELETE …/galleries/{galleryId}/identities/{identityId} (F4, #36).

Toda respuesta declarada pasa por el arnés (`tests/support/identities.checked`).

LA CASCADA, Y QUÉ PASA SI SE CORTA. Sin transacciones, el orden es: marcar la
identidad, borrar sus rostros, descontarlos de la galería, borrar la identidad
y descontarla (`src/controllers/identities.delete_identity`). En el orden
inverso, una caída dejaría rostros ---datos biométricos--- de una identidad que
ya no existe, y ninguna operación los alcanzaría. Se prueba cortándola DE
VERDAD, como en `test_delete_gallery.py`: en los dos puntos la identidad sigue
visible y repetir el `DELETE` termina el trabajo. Los contadores de la galería
pueden quedar por encima tras la caída: es de la reconciliación y no se prueba.

EL ALCANCE: la identidad vecina, de la misma galería, sale intacta.

La carrera con `addFace`, en `test_races.py`.
"""

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.asynchronous.collection import AsyncCollection

from src.main import create_app
from tests.conftest import BASE_URL
from tests.contract.harness import assert_bare_error
from tests.support.identities import (
    IDENTITY,
    assert_not_found,
    delete,
    faces_of,
    fetch,
    state,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.plan import with_fake_space
from tests.support.seed import seed_gallery, seed_identity_with_faces
from tests.support.settings import settings


@pytest.fixture
def gallery(db):
    return seed_gallery(db, ACCOUNT_A, identityCount=2, faceCount=5)


@pytest.fixture
def identity(db, gallery):
    return seed_identity_with_faces(db, gallery, faces=3)


@pytest.fixture
def neighbour(db, gallery):
    return seed_identity_with_faces(db, gallery, faces=2)


def left(db, identity_id):
    """(identidad, rostros) que quedan de esa identidad."""
    return (
        db.identities.count_documents({"_id": ObjectId(identity_id)}),
        len(faces_of(db, identity_id)),
    )


# ---------------------------------------------------------------- 204


def test_204_deletes_the_identity_and_its_faces_and_discounts_them(
    client, db, gallery, identity, neighbour
):
    res = delete(client, gallery, identity)

    assert res.status_code == 204, res.text
    assert left(db, identity) == (0, 0)
    assert left(db, neighbour) == (1, 2)
    assert state(db, gallery) == (1, 2, 1, 2)
    assert_not_found(fetch(client, gallery, identity), "Identity")


def test_204_on_a_frozen_gallery(client, db):
    frozen = seed_gallery(db, ACCOUNT_A, status="frozen", frozenReason="MODEL_NOT_IN_PLAN")
    identity = seed_identity_with_faces(db, frozen, faces=1)

    assert delete(client, frozen, identity).status_code == 204
    assert left(db, identity) == (0, 0)


def test_404_deleting_twice(client, db, gallery, identity):
    assert delete(client, gallery, identity).status_code == 204

    assert_not_found(delete(client, gallery, identity), "Identity")
    assert state(db, gallery) == (0, 0, 1, 2)


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_the_gallery_of_another_account_and_nothing_is_deleted(
    client, db, gallery, identity, role
):
    res = delete(client, gallery, identity, gateway_headers(ACCOUNT_B, role=role))

    assert_not_found(res, "Gallery")
    assert left(db, identity) == (1, 3)
    assert "deletingAt" not in db.identities.find_one({"_id": ObjectId(identity)})


def test_404_an_identity_of_another_gallery_of_the_same_account(client, db, identity):
    other = seed_gallery(db, ACCOUNT_A, name="Otra")

    assert_not_found(delete(client, other, identity), "Identity")
    assert left(db, identity) == (1, 3)
    assert "deletingAt" not in db.identities.find_one({"_id": ObjectId(identity)})


@pytest.mark.parametrize("identity_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_an_identity_that_does_not_or_cannot_exist(client, db, gallery, identity, identity_id):
    assert_not_found(delete(client, gallery, identity_id), "Identity")
    assert left(db, identity) == (1, 3)


# ------------------------------------------------- la cascada, cortada

#: Dónde se corta y qué tiene que quedar de la identidad: (identidad, rostros).
CRASHES = {
    "cae al descontar los rostros de la galería": ("galleries", "update_one", (1, 0)),
    "cae al borrar la identidad": ("identities", "delete_one", (1, 0)),
}


@pytest.mark.parametrize("collection,method,expected", CRASHES.values(), ids=CRASHES.keys())
def test_a_cascade_cut_halfway_leaves_the_identity_visible_and_a_retry_finishes_it(
    test_db_uri, db, gallery, identity, neighbour, monkeypatch, collection, method, expected
):
    original = getattr(AsyncCollection, method)

    async def crash(self, *args, **kwargs):
        if self.name == collection:
            raise RuntimeError("caída inyectada")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AsyncCollection, method, crash)
    app = with_fake_space(create_app(settings(test_db_uri)))
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
        res = client.delete(IDENTITY.format(gallery, identity), headers=gateway_headers())

        assert res.status_code == 500, res.text
        assert res.json() == {"code": "INTERNAL_ERROR", "message": "Internal server error"}
        assert_bare_error(res)
        assert left(db, identity) == expected
        # Visible: la identidad sigue ahí para que el cliente repita el DELETE.
        assert fetch(client, gallery, identity).status_code == 200

        monkeypatch.undo()

        assert delete(client, gallery, identity).status_code == 204
        assert left(db, identity) == (0, 0)
        assert left(db, neighbour) == (1, 2)
