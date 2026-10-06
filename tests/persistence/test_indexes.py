"""Los índices que crea el servicio al arrancar, contra MongoDB REAL (docs#34).

Lo esperado se escribe AQUÍ, literal, y no se lee de `src/documents`: una prueba
que comparara `INDEXES` consigo mismo no vería un índice mal declarado.

Cuatro promesas:

  1. FORMA. Cada colección tiene exactamente estos índices, con su clave, su
     unicidad y su filtro. Se compara `index_information()` entero, así que un
     `sparse` o una intercalación que nadie decidió también ponen esto en rojo.
  2. UNICIDAD. Rechaza el duplicado, y admite lo que debe admitir. Las
     inserciones van por el cliente síncrono, directas a la colección, después
     de que el servicio haya arrancado y creado los índices.
  3. IDEMPOTENCIA. Arrancar dos veces no cambia nada.
  4. NEGATIVA A ARRANCAR. Si un índice no se puede crear ---duplicados previos o
     un índice viejo con el mismo nombre y otra forma---, el servicio no arranca.
     El porqué está junto a `ensure_indexes`.

El fixture `db` borra las colecciones con `drop` antes de cada prueba, índices
incluidos: cada arranque del servicio los estrena.
"""

import json

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, OperationFailure

from src.core.settings import Settings
from src.main import create_app
from tests.conftest import BASE_URL

ID = {"v": 2, "key": [("_id", ASCENDING)]}

EXPECTED = {
    "galleries": {
        "_id_": ID,
        "accountId_1_name_1": {
            "v": 2,
            "key": [("accountId", ASCENDING), ("name", ASCENDING)],
            "unique": True,
        },
    },
    "identities": {
        "_id_": ID,
        "galleryId_1": {"v": 2, "key": [("galleryId", ASCENDING)]},
        "galleryId_1_externalId_1": {
            "v": 2,
            "key": [("galleryId", ASCENDING), ("externalId", ASCENDING)],
            "unique": True,
            "partialFilterExpression": {"externalId": {"$type": "string"}},
        },
    },
    "faces": {
        "_id_": ID,
        "galleryId_1": {"v": 2, "key": [("galleryId", ASCENDING)]},
        "identityId_1": {"v": 2, "key": [("identityId", ASCENDING)]},
    },
}


def _indexes(db) -> dict:
    return {name: db[name].index_information() for name in EXPECTED}


def _start(test_db_uri) -> None:
    """Un arranque completo del servicio, con su `lifespan`."""
    with TestClient(create_app(Settings(mongodb_uri=test_db_uri)), base_url=BASE_URL):
        pass


# ------------------------------------------------------------------ 1. forma


@pytest.mark.parametrize("collection", EXPECTED)
def test_each_collection_has_exactly_these_indexes(client, db, collection):
    assert db[collection].index_information() == EXPECTED[collection]


# --------------------------------------------------------- 2. unicidad: galerías


def _gallery(**fields) -> dict:
    return {"accountId": "acc-a", "name": "Empleados", **fields}


def test_a_repeated_gallery_name_in_the_same_account_is_rejected(client, db):
    db.galleries.insert_one(_gallery())

    with pytest.raises(DuplicateKeyError) as exc:
        db.galleries.insert_one(_gallery())

    # F3 traduce por el índice que saltó, no solo por el 11000.
    assert exc.value.details["keyPattern"] == {"accountId": 1, "name": 1}


@pytest.mark.parametrize(
    "other",
    [_gallery(accountId="acc-b"), _gallery(name="empleados"), _gallery(name="Empleados ")],
    ids=["otra cuenta", "otra caja (comparación exacta)", "espacio final (comparación exacta)"],
)
def test_the_gallery_name_index_admits(client, db, other):
    db.galleries.insert_one(_gallery())

    db.galleries.insert_one(other)

    assert db.galleries.count_documents({}) == 2


# ------------------------------------------------------ 2. unicidad: identidades

GALLERY_A = ObjectId("66f0000000000000000000aa")
GALLERY_B = ObjectId("66f0000000000000000000bb")


def _identity(gallery=GALLERY_A, **fields) -> dict:
    return {"galleryId": gallery, "fullName": "Ana", **fields}


def test_a_repeated_external_id_in_the_same_gallery_is_rejected(client, db):
    db.identities.insert_one(_identity(externalId="E-1"))

    with pytest.raises(DuplicateKeyError) as exc:
        db.identities.insert_one(_identity(externalId="E-1"))

    assert exc.value.details["keyPattern"] == {"galleryId": 1, "externalId": 1}


def test_an_empty_external_id_is_a_value_and_collides(client, db):
    # Por esto el contrato exige `minLength: 1`: el índice parcial indexa "", y
    # dos "" en la misma galería chocan. El código no normaliza "" a ausente.
    db.identities.insert_one(_identity(externalId=""))

    with pytest.raises(DuplicateKeyError):
        db.identities.insert_one(_identity(externalId=""))


@pytest.mark.parametrize(
    "first,second",
    [
        (_identity(externalId="E-1"), _identity(GALLERY_B, externalId="E-1")),
        (_identity(), _identity()),
        (_identity(externalId=None), _identity(externalId=None)),
    ],
    ids=["mismo externalId en otra galería", "dos sin externalId", "dos con externalId null"],
)
def test_the_external_id_index_admits(client, db, first, second):
    # Los dos últimos son los casos 3 y 4 de la medida de #34, ahora contra el
    # índice que crea el servicio. Con el disperso de CLAUDE.md, ambos fallaban.
    db.identities.insert_one(first)

    db.identities.insert_one(second)

    assert db.identities.count_documents({}) == 2


# ------------------------------------------------------------ 3. idempotencia


def test_starting_twice_changes_nothing(test_db_uri, db):
    _start(test_db_uri)
    first = _indexes(db)

    _start(test_db_uri)

    assert _indexes(db) == first == EXPECTED


# ---------------------------------------------------- 4. negativa a arrancar


def _events(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_it_refuses_to_start_if_existing_data_breaks_a_unique_index(test_db_uri, db, capsys):
    db.galleries.insert_many([_gallery(), _gallery()])

    with pytest.raises(DuplicateKeyError) as exc:
        _start(test_db_uri)

    assert exc.value.code == 11000
    failed = [e for e in _events(capsys.readouterr().out) if e["event"] == "mongodb_index_failed"]
    assert failed == [
        {
            "event": "mongodb_index_failed",
            "collection": "galleries",
            "code": 11000,
            "codeName": "DuplicateKey",
            "logger": "src.core.database",
            "level": "error",
            "timestamp": failed[0]["timestamp"],
        }
    ]


def test_it_refuses_to_start_over_an_old_index_with_the_same_name_and_another_shape(
    test_db_uri, db
):
    # El índice tal como lo describía CLAUDE.md: disperso. Cambiar la forma de un
    # índice exige una migración explícita; el arranque no la hace por su cuenta.
    db.identities.create_index(
        [("galleryId", ASCENDING), ("externalId", ASCENDING)], unique=True, sparse=True
    )

    with pytest.raises(OperationFailure) as exc:
        _start(test_db_uri)

    assert exc.value.code == 86  # IndexKeySpecsConflict, medido en #34
