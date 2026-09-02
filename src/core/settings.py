from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = "TFM Python Microservice Template"
    version: str = "0.1.0"
    api_v1_str: str = "/api/v1"

    # Entorno
    environment: str = "development"
    debug: bool = False

    # CORS
    cors_origins: list[str] = ["*"]

    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "tfm_microservice"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
