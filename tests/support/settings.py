"""Configuración de las pruebas que construyen la aplicación a mano.

`EMBEDDING_TOKEN_SECRET`, `SPACE_URL` y `SPACE_API_KEY` no tienen valor por
omisión (src/core/settings.py), así que toda prueba que cree un `Settings`
tiene que dárselos. Estos literales solo existen en las pruebas.
"""

from src.core.settings import Settings

TOKEN_SECRET = "test-only-embedding-token-secret"

UNUSED_DB = "mongodb://unused.invalid:27017/unused"

#: SPACE tampoco tiene valores por omisión. Las pruebas no lo alcanzan nunca:
#: lo sustituye un doble (tests/support/plan.py) por `dependency_overrides`.
SPACE_URL = "http://space.invalid:3000"
SPACE_API_KEY = "org_test-only-space-api-key"


def settings(mongodb_uri: str = UNUSED_DB, **overrides) -> Settings:
    values = {
        "embedding_token_secret": TOKEN_SECRET,
        "space_url": SPACE_URL,
        "space_api_key": SPACE_API_KEY,
        **overrides,
    }
    return Settings(mongodb_uri=mongodb_uri, **values)
