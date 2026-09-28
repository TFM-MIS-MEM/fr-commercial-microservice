"""Andamiaje común.

MongoDB REAL, no un doble en memoria: lo que se prueba es que la cuenta forma
parte del filtro de la consulta, y un doble reproduciría la consulta que yo
creo que hago, no la que hace el servidor.

`TEST_MONGODB_URI` apunta a un SERVIDOR; la base de datos la elige la sesión
(`fr_test_<aleatorio>`) y la borra al acabar. Es una variable distinta de
`MONGODB_URI` a propósito: unas pruebas que borran su base no deben poder
apuntar, por reutilizar la variable, a la de desarrollo. Sin ella las pruebas que
necesitan base FALLAN, no se saltan: una prueba que se salta en silencio es una
prueba que pasa sin mirar.

La siembra va por un cliente SÍNCRONO, directo a la colección, y el servicio se
arranca con `TestClient` como gestor de contexto, que es lo que ejecuta su
`lifespan`: se prueba la conexión que el servicio abre él mismo.
"""

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from src.core.settings import Settings
from src.main import create_app

# Un `servers` del contrato: openapi-core rechaza un anfitrión que no figure en
# él (`ServerNotFound ... http://testserver`), medido en #33.
BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
def test_db_uri() -> str:
    server = os.environ.get("TEST_MONGODB_URI", "").strip()
    if not server:
        pytest.fail(
            "TEST_MONGODB_URI no está definida. Apúntela a un servidor MongoDB "
            "(sin base de datos): la sesión crea y borra la suya.",
            pytrace=False,
        )
    parts = urlsplit(server)
    if parts.path not in ("", "/"):
        pytest.fail("TEST_MONGODB_URI no debe nombrar base de datos.", pytrace=False)
    name = f"fr_test_{uuid.uuid4().hex[:12]}"
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture(scope="session")
def sync_db(test_db_uri):
    client = MongoClient(test_db_uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    db = client.get_default_database()
    yield db
    client.drop_database(db.name)
    client.close()


@pytest.fixture
def db(sync_db):
    """Base limpia por prueba: ninguna depende del orden ni de otra."""
    for name in sync_db.list_collection_names():
        sync_db.drop_collection(name)
    return sync_db


@pytest.fixture
def client(test_db_uri, db):
    app = create_app(Settings(mongodb_uri=test_db_uri))
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client
