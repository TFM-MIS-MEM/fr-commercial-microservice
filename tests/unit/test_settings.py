"""Guardas de arranque de MONGODB_URI y EMBEDDING_TOKEN_SECRET (plano del código).

El plano de la orquestación ---`${MONGODB_URI:?}` en el compose de producción---
se comprueba con `docker compose config`, no aquí.

Sin literales publicados todavía: la URI no lleva credencial (docs#106). Cuando
la lleve, esta prueba tendrá que leer los `.env*.example` y exigir que la guarda
rechace cualquier valor que aparezca en ellos.
"""

import pytest

from src.core.settings import ConfigurationError, Settings, load_settings
from src.main import create_app
from tests.support.settings import TOKEN_SECRET

VALID = "mongodb://fr-commercial-mongodb:27017/fr-commercial-db"


@pytest.fixture(autouse=True)
def _token_secret(monkeypatch):
    """Las pruebas de MONGODB_URI no dependen del secreto: se lo da el entorno.

    Las del secreto lo quitan o lo cambian ellas mismas. En el contenedor de
    desarrollo el compose ya lo define; así la cifra no depende de dónde corra.
    """
    monkeypatch.setenv("EMBEDDING_TOKEN_SECRET", TOKEN_SECRET)


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
