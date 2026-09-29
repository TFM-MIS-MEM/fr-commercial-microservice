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

SIN SERVIDOR, UN SOLO FALLO Y CLARO. La sesión hace `ping` una vez antes de la
primera prueba que necesita base. Si no responde, falla con qué levantar, y
pytest reutiliza ese fallo en las demás pruebas con base sin volver a
intentarlo. Antes de esta comprobación (#33), sin servidor, cada prueba agotaba
por su cuenta los 5 s de selección de servidor: 37 errores repitiendo
`ServerSelectionTimeoutError`, 207 s. El código de salida sigue siendo distinto
de cero, y las pruebas que no usan base se ejecutan igual.
"""

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

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
    try:
        client.admin.command("ping")
        failure = None
    except PyMongoError as exc:
        failure = type(exc).__name__
    if failure:
        # Fuera del `except`: dentro, pytest imprimiría antes la excepción
        # encadenada y el mensaje quedaría enterrado debajo.
        # Solo anfitrión y puerto: la URI podría llevar credencial.
        seeds = client.topology_description.server_descriptions()
        server = ",".join(f"{host}:{port}" for host, port in seeds)
        client.close()
        pytest.fail(
            f"MongoDB no responde en {server} (TEST_MONGODB_URI): {failure}.\n"
            "Levántelo antes de ejecutar las pruebas:\n"
            "  docker compose -f docker-compose.dev.yml up -d fr-commercial-mongodb\n"
            "y exporte TEST_MONGODB_URI=mongodb://localhost:27019 (servidor, sin base).",
            pytrace=False,
        )
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
