"""Configuración del servicio.

NINGÚN parámetro que solo el despliegue pueda fijar arranca con un valor por
omisión. La plantilla de la que nace este servicio traía
`mongo_uri = "mongodb://localhost:27017"`: un despliegue sin configurar se
habría levantado contra otra base de datos sin dar un solo error, que es el
fallo silencioso que la sesión 8 retiró de `auth` (#95).

La guarda existe en DOS planos, o no existe:

  - Aquí, en el código: el servicio no arranca si `MONGODB_URI` falta, está en
    blanco o no nombra la base de datos.
  - En la orquestación: `docker-compose.yml` la exige con `${MONGODB_URI:?}` y
    `docker-compose.dev.yml` la fija sin `:-`. Un valor por omisión en el
    compose anularía esta guarda, porque ésta nunca llegaría a ver la variable
    ausente.

Hoy la URI no lleva credencial ---el MongoDB de este servicio no tiene
autenticación, TFM-MIS-MEM/docs#106---, así que no hay literal publicado que
rechazar. Cuando la lleve, la guarda tendrá que rechazar también los valores que
el repositorio divulgue como ejemplo, con el mismo criterio que
`auth-microservice/src/config/internalSecret.js`: se rechaza lo que CONSTA
público, no lo que parece débil.

`EMBEDDING_TOKEN_SECRET` sigue la misma regla: sin valor por omisión, y el
servicio no arranca si falta o está en blanco. `docker-compose.yml` la exige con
`:?`; `docker-compose.dev.yml` fija un valor de desarrollo explícito. El servicio
no distingue hoy el entorno de desarrollo, así que no puede rechazar ese literal
fuera de él: es TFM-MIS-MEM/docs#117, y hasta F7 nadie verifica el token.

`SPACE_URL` y `SPACE_API_KEY` (F10, #42), igual: sin valor por omisión, y el
servicio no arranca si faltan. Sin el evaluador de planes no hay operación que
se pueda admitir. La clave tiene que ser de ORGANIZACIÓN (`org_…`): con una de
usuario SPACE no evalúa, y el fallo no se vería hasta la primera petición. La
emite `scripts/space-bootstrap.sh`, que la escribe en el `.env` de este
repositorio, de donde la toma `docker-compose.dev.yml`.

No se lee ningún fichero `.env` desde aquí. La configuración entra por el
entorno del proceso ---el compose la pone ahí--- y en ningún otro sitio: un
`.env` leído en silencio es una segunda fuente que las pruebas no controlan.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo.errors import InvalidURI
from pymongo.uri_parser import parse_uri

#: Los pesos viajan con el código (`checkpoints/`, en la raíz del repositorio y
#: copiado en la imagen): su ubicación es un hecho del repositorio, no del
#: despliegue, y por eso tiene valor por omisión.
CHECKPOINTS_DIR = Path(__file__).resolve().parents[2] / "checkpoints"


class ConfigurationError(RuntimeError):
    """El servicio se niega a arrancar con esta configuración."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Sin valor por omisión: véase la cabecera del fichero.
    mongodb_uri: str

    # Con valor por omisión, y legítimo: está acotado a un conjunto cerrado,
    # y equivocarse aquí solo cambia cuánto se escribe en el registro.
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Sin valor por omisión, como `mongodb_uri`. `SecretStr` para que no salga
    # en claro en un `repr` ni en un registro.
    embedding_token_secret: SecretStr

    # El evaluador de planes (src/core/plan.py). Sin valor por omisión.
    space_url: str
    space_api_key: SecretStr

    checkpoints_dir: Path = CHECKPOINTS_DIR

    # Para la campaña de medición. Por omisión, los hilos que elija torch (uno
    # por CPU visible) y una sola inferencia a la vez; los valores de la campaña
    # se fijan en la sesión del instrumento.
    torch_num_threads: int | None = Field(default=None, ge=1)
    inference_concurrency: int = Field(default=1, ge=1)

    @field_validator("mongodb_uri")
    @classmethod
    def _mongodb_uri(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("MONGODB_URI está en blanco")
        try:
            parsed = parse_uri(value)
        except (InvalidURI, ValueError) as exc:
            raise ValueError(f"MONGODB_URI no es una URI de MongoDB válida: {exc}") from None
        if not parsed.get("database"):
            # Sin base de datos en la URI, el cliente no sabe cuál usar y la
            # tentación es poner un nombre fijo en el código: otro valor por
            # omisión con otro nombre.
            raise ValueError("MONGODB_URI no nombra la base de datos (…/<base>)")
        # Sin normalizar: lo que se usa es la cadena tal cual la fijó el
        # despliegue.
        return value

    @field_validator("embedding_token_secret")
    @classmethod
    def _embedding_token_secret(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value().strip() == "":
            raise ValueError("EMBEDDING_TOKEN_SECRET está en blanco")
        return value

    @field_validator("space_url")
    @classmethod
    def _space_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("SPACE_URL no es una URL http(s) con anfitrión")
        return value.strip().rstrip("/")

    @field_validator("space_api_key")
    @classmethod
    def _space_api_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value().strip()
        if key == "":
            raise ValueError("SPACE_API_KEY está en blanco")
        if not key.startswith("org_"):
            raise ValueError("SPACE_API_KEY no es una clave de organización de SPACE (org_…)")
        return value


def load_settings() -> Settings:
    """Lee la configuración del entorno o se niega a arrancar diciendo por qué."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'/'.join(str(p) for p in err['loc']).upper()}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigurationError(
            f"Configuración rechazada, el servicio no arranca. {problems}"
        ) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
