"""deleteGallery — DELETE /v1/fr/commercial/galleries/{galleryId} (F3, #35).

Toda respuesta declarada pasa por el arnés (`tests/support/galleries.checked`).

LA CASCADA, Y QUÉ PASA SI SE CORTA. Sin transacciones, el orden es rostros,
identidades y la galería al final (`src/controllers/galleries.delete_gallery`).
Se prueba cortándola DE VERDAD, contra MongoDB real: la escritura de un paso
lanza una excepción, y se comprueba lo que queda. En los dos puntos de caída la
galería sigue visible y repetir el `DELETE` termina el trabajo. Ese `500` no es
una respuesta declarada, así que no pasa por el arnés; pero tiene que ser un
error desnudo, sin traza.

EL ALCANCE: otra galería de la misma cuenta, con sus identidades y rostros, sale
intacta de cada borrado. Sin esa galería vecina, un `delete_many({})` pasaría
todas estas pruebas.
"""

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.asynchronous.collection import AsyncCollection

from src.main import create_app
from tests.conftest import BASE_URL
from tests.contract.harness import assert_bare_error
from tests.support.galleries import (
    ONE,
    assert_not_found,
    assert_owner_sees_it,
    delete,
    fetch,
)
from tests.support.identity import ACCOUNT_A, ACCOUNT_B, gateway_headers
from tests.support.seed import seed_gallery, seed_identity_with_faces
from tests.support.settings import settings


def contents(db, gallery_id):
    """Lo que hay en la base de una galería: (galería, identidades, rostros)."""
    oid = ObjectId(gallery_id)
    return (
        db.galleries.count_documents({"_id": oid}),
        db.identities.count_documents({"galleryId": oid}),
        db.faces.count_documents({"galleryId": oid}),
    )


@pytest.fixture
def populated(db):
    """Dos galerías de ACCOUNT_A, cada una con 2 identidades de 2 rostros."""
    target, neighbour = seed_gallery(db, ACCOUNT_A), seed_gallery(db, ACCOUNT_A, name="Vecina")
    for gallery_id in (target, neighbour):
        for _ in range(2):
            seed_identity_with_faces(db, gallery_id, faces=2)
    assert contents(db, target) == contents(db, neighbour) == (1, 2, 4)
    return target, neighbour


# ---------------------------------------------------------------- 204


def test_204_deletes_the_gallery_its_identities_and_faces_and_nothing_else(client, db, populated):
    target, neighbour = populated

    res = delete(client, target)

    assert res.status_code == 204, res.text
    assert res.content == b""
    assert contents(db, target) == (0, 0, 0)
    assert contents(db, neighbour) == (1, 2, 4)
    assert_not_found(fetch(client, target))


def test_204_a_frozen_gallery_can_be_deleted(client, db):
    # Para que el cliente pueda liberar cuota (descripción de la operación).
    gallery_id = seed_gallery(db, ACCOUNT_A, status="frozen", frozenReason="MODEL_NOT_IN_PLAN")

    assert delete(client, gallery_id).status_code == 204
    assert contents(db, gallery_id) == (0, 0, 0)


def test_404_deleting_twice(client, db, populated):
    target, _ = populated
    assert delete(client, target).status_code == 204

    assert_not_found(delete(client, target))


# ---------------------------------------------------------------- 404


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
def test_404_another_account_cannot_delete_it_and_nothing_is_deleted(client, db, populated, role):
    target, _ = populated

    assert_not_found(delete(client, target, gateway_headers(ACCOUNT_B, role=role)))
    assert contents(db, target) == (1, 2, 4)
    assert_owner_sees_it(client, target)


@pytest.mark.parametrize("gallery_id", [str(ObjectId()), "no-es-un-objectid"])
def test_404_a_gallery_that_does_not_or_cannot_exist(client, db, populated, gallery_id):
    target, _ = populated

    assert_not_found(delete(client, gallery_id))
    assert contents(db, target) == (1, 2, 4)


# ------------------------------------------------- la cascada, cortada

#: Dónde se corta y qué tiene que quedar: (galería, identidades, rostros).
CRASHES = {
    "cae al borrar las identidades": ("identities", "delete_many", (1, 2, 0)),
    "cae al borrar la galería": ("galleries", "delete_one", (1, 0, 0)),
}


@pytest.mark.parametrize("collection,method,left", CRASHES.values(), ids=CRASHES.keys())
def test_a_cascade_cut_halfway_leaves_the_gallery_visible_and_a_retry_finishes_it(
    test_db_uri, db, populated, monkeypatch, collection, method, left
):
    target, neighbour = populated
    original = getattr(AsyncCollection, method)

    async def crash(self, *args, **kwargs):
        if self.name == collection:
            raise RuntimeError("caída inyectada")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AsyncCollection, method, crash)
    app = create_app(settings(test_db_uri))
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
        res = client.delete(ONE.format(target), headers=gateway_headers())

        assert res.status_code == 500, res.text
        assert res.json() == {"code": "INTERNAL_ERROR", "message": "Internal server error"}
        assert_bare_error(res)
        assert contents(db, target) == left
        # Visible: la galería sigue ahí para que el cliente repita el DELETE.
        assert_owner_sees_it(client, target)

        monkeypatch.undo()

        assert delete(client, target).status_code == 204
        assert contents(db, target) == (0, 0, 0)
        assert contents(db, neighbour) == (1, 2, 4)
