"""Configuración de las pruebas que construyen la aplicación a mano.

`EMBEDDING_TOKEN_SECRET` no tiene valor por omisión (src/core/settings.py), así
que toda prueba que cree un `Settings` tiene que dárselo. Este literal solo
existe en las pruebas.
"""

from src.core.settings import Settings

TOKEN_SECRET = "test-only-embedding-token-secret"

UNUSED_DB = "mongodb://unused.invalid:27017/unused"


def settings(mongodb_uri: str = UNUSED_DB, **overrides) -> Settings:
    return Settings(mongodb_uri=mongodb_uri, embedding_token_secret=TOKEN_SECRET, **overrides)
