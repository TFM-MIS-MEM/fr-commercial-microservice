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

No se lee ningún fichero `.env` desde aquí. La configuración entra por el
entorno del proceso ---el compose la pone ahí--- y en ningún otro sitio: un
`.env` leído en silencio es una segunda fuente que las pruebas no controlan.
"""

from functools import lru_cache
from typing import Literal

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo.errors import InvalidURI
from pymongo.uri_parser import parse_uri


class ConfigurationError(RuntimeError):
    """El servicio se niega a arrancar con esta configuración."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Sin valor por omisión: véase la cabecera del fichero.
    mongodb_uri: str

    # Con valor por omisión, y legítimo: está acotado a un conjunto cerrado,
    # y equivocarse aquí solo cambia cuánto se escribe en el registro.
    log_level: Literal["debug", "info", "warning", "error"] = "info"

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
