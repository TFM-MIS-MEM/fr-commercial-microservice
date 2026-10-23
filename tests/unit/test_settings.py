"""Guardas de arranque de MONGODB_URI, EMBEDDING_TOKEN_SECRET, SPACE_URL y
SPACE_API_KEY (plano del código).

El plano de la orquestación ---`${MONGODB_URI:?}` en el compose de producción---
se comprueba con `docker compose config`, no aquí.

Sin literales publicados todavía: la URI no lleva credencial (docs#106). Cuando
la lleve, esta prueba tendrá que leer los `.env*.example` y exigir que la guarda
rechace cualquier valor que aparezca en ellos.
"""

import pytest

from src.core.settings import ConfigurationError, Settings, load_settings
from src.main import create_app
from tests.support.settings import SPACE_API_KEY, SPACE_URL, TOKEN_SECRET

VALID = "mongodb://fr-commercial-mongodb:27017/fr-commercial-db"


@pytest.fixture(autouse=True)
def _token_secret(monkeypatch):
    """Las pruebas de MONGODB_URI no dependen del secreto: se lo da el entorno.

    Las del secreto lo quitan o lo cambian ellas mismas. En el contenedor de
    desarrollo el compose ya lo define; así la cifra no depende de dónde corra.
    """
    monkeypatch.setenv("EMBEDDING_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setenv("SPACE_URL", SPACE_URL)
    monkeypatch.setenv("SPACE_API_KEY", SPACE_API_KEY)


def test_absent_refuses_to_start(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)

    with pytest.raises(ConfigurationError, match="MONGODB_URI"):
        load_settings()


def test_the_app_factory_refuses_to_start_without_it(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)

    with pytest.raises(ConfigurationError):
        create_app()


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_refuses_to_start(monkeypatch, blank):
    monkeypatch.setenv("MONGODB_URI", blank)

    with pytest.raises(ConfigurationError, match="en blanco"):
        load_settings()


def test_a_uri_without_database_refuses_to_start(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://fr-commercial-mongodb:27017")

    with pytest.raises(ConfigurationError, match="base de datos"):
        load_settings()


def test_something_that_is_not_a_mongodb_uri_refuses_to_start(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "postgres://x/y")

    with pytest.raises(ConfigurationError, match="URI de MongoDB"):
        load_settings()


def test_a_valid_uri_is_taken_as_is(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", VALID)

    assert load_settings().mongodb_uri == VALID


def test_log_level_is_a_closed_set(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        load_settings()


def test_log_level_default_is_info():
    assert Settings(mongodb_uri=VALID).log_level == "info"


def test_without_embedding_token_secret_refuses_to_start(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.delenv("EMBEDDING_TOKEN_SECRET")

    with pytest.raises(ConfigurationError, match="EMBEDDING_TOKEN_SECRET"):
        load_settings()


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_embedding_token_secret_refuses_to_start(monkeypatch, blank):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.setenv("EMBEDDING_TOKEN_SECRET", blank)

    with pytest.raises(ConfigurationError, match="EMBEDDING_TOKEN_SECRET está en blanco"):
        load_settings()


def test_the_embedding_token_secret_never_shows_in_clear(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", VALID)

    loaded = load_settings()

    assert TOKEN_SECRET not in repr(loaded)
    assert loaded.embedding_token_secret.get_secret_value() == TOKEN_SECRET


@pytest.mark.parametrize("name", ["TORCH_NUM_THREADS", "INFERENCE_CONCURRENCY"])
def test_campaign_knobs_reject_zero(monkeypatch, name):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.setenv(name, "0")

    with pytest.raises(ConfigurationError, match=name):
        load_settings()


def test_campaign_knobs_default_to_torch_choice_and_one_at_a_time(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.delenv("TORCH_NUM_THREADS", raising=False)
    monkeypatch.delenv("INFERENCE_CONCURRENCY", raising=False)

    loaded = load_settings()

    assert loaded.torch_num_threads is None
    assert loaded.inference_concurrency == 1


# ------------------------------------------------------------------ SPACE (F10)


@pytest.mark.parametrize("name", ["SPACE_URL", "SPACE_API_KEY"])
def test_without_space_refuses_to_start(monkeypatch, name):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.delenv(name)

    with pytest.raises(ConfigurationError, match=name):
        load_settings()


@pytest.mark.parametrize("url", ["", "space-server:3000", "ftp://space-server", "http://"])
def test_a_space_url_that_is_not_http_refuses_to_start(monkeypatch, url):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.setenv("SPACE_URL", url)

    with pytest.raises(ConfigurationError, match="SPACE_URL"):
        load_settings()


@pytest.mark.parametrize("key", ["", "   ", "usr_a-user-key", "a-key"])
def test_a_space_api_key_that_is_not_an_organization_key_refuses_to_start(monkeypatch, key):
    """Con una clave de usuario SPACE no evalúa: se vería en la primera petición."""
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.setenv("SPACE_API_KEY", key)

    with pytest.raises(ConfigurationError, match="SPACE_API_KEY"):
        load_settings()


def test_space_is_taken_without_trailing_slash_and_the_key_never_in_clear(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", VALID)
    monkeypatch.setenv("SPACE_URL", "http://space-server:3000/")

    loaded = load_settings()

    assert loaded.space_url == "http://space-server:3000"
    assert SPACE_API_KEY not in repr(loaded)
