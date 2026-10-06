"""Conexión con MongoDB.

DESVIACIÓN DE LA PLANTILLA, deliberada: `python-template` usa `motor`, y aquí se
usa la API asíncrona de PyMongo (`pymongo.AsyncMongoClient`). Los metadatos del
propio paquete `motor==3.7.1` lo dicen: «Motor will be deprecated on May 14th,
2026, one year after the production release of the PyMongo Async driver». El
servicio nace después de esa fecha. La plantilla queda avisada en
TFM-MIS-MEM/docs#107.

`tz_aware=True` no es cosmético: sin él PyMongo devuelve fechas sin zona, se
serializan sin sufijo y dejan de ser `date-time` válidas en RFC 3339, que es lo
que declara el contrato.
"""

from fastapi import Request
from pymongo import AsyncMongoClient, IndexModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

from src.core.logger import get_logger

logger = get_logger(__name__)

SERVER_SELECTION_TIMEOUT_MS = 5000


async def connect(uri: str) -> AsyncMongoClient:
    """Abre el cliente y comprueba que el servidor responde; si no, no arranca."""
    client: AsyncMongoClient = AsyncMongoClient(
        uri, tz_aware=True, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS
    )
    try:
        await client.admin.command("ping")
    except Exception:
        await client.close()
        raise
    return client


async def ensure_indexes(database: AsyncDatabase, indexes: dict[str, list[IndexModel]]) -> None:
    """Crea los índices declarados en `src/documents`. Idempotente.

    Crear un índice que ya existe con la misma forma no hace nada, así que
    arrancar dos veces no cambia nada (`tests/persistence/test_indexes.py`).

    SI UNO FALLA, EL SERVICIO NO ARRANCA: la excepción sale del `lifespan`.
    Arrancar sin él sería peor que no arrancar:

      - Sin un índice único, el `409` que declara el contrato pasa a ser
        mentira en silencio, y los duplicados se acumulan hasta que el índice
        ya no se puede crear sin limpiar a mano.
      - Sin `{galleryId}` en `faces`, la búsqueda 1:N degrada a COLLSCAN, en el
        camino caliente, sin que nada avise.

    El precio es de disponibilidad, también para las lecturas, y es visible.
    Medido en #34 con duplicados previos en `galleries`; el registro lleva
    `mongodb_index_failed` con colección, índice y código, y uvicorn escribe
    `ERROR:    Application startup failed. Exiting.`:

      - `docker-compose.dev.yml` (uvicorn con `--reload`): el contenedor NO
        sale. El proceso de recarga sigue vivo, el de la aplicación no, y el
        healthcheck lo marca: `running` y `health=starting` hasta los ~55 s,
        luego `unhealthy`, `RestartCount` 0. `docker ps`: «Up About a minute
        (unhealthy)». Nada lo reinicia: corregidos los datos, volvió a
        `healthy` con `docker restart`.
      - La imagen de `Dockerfile` (sin `--reload`) con `restart:
        unless-stopped`, que es lo que fija `docker-compose.yml`: el proceso
        sale con código 3 y Docker lo reinicia en bucle con espera creciente,
        «Restarting (3)», 10 reinicios en ~65 s. Se midió con `docker run` y
        la misma política, no con ese compose: sus `container_name` chocan con
        los del entorno de desarrollo. Ese compose no declara healthcheck.

    CAMBIAR LA FORMA DE UN ÍNDICE EXIGE UNA MIGRACIÓN EXPLÍCITA. Un índice viejo
    con el mismo nombre y otra forma ---el disperso que describía CLAUDE.md, por
    ejemplo--- da `IndexKeySpecsConflict` (86) y el arranque se niega (medido en
    #34). El arranque no borra ni rehace índices por su cuenta: hacerlo sobre
    una colección grande es una operación larga y, en un único, puede fallar a
    medias por los datos.

    Uno a uno y no en lote por colección, para que el registro diga cuál falló.
    Al registro van colección, índice y código; NO el mensaje del servidor, que
    en un duplicado lleva los valores repetidos, datos del cliente.
    """
    for collection, models in indexes.items():
        for model in models:
            name = model.document["name"]
            try:
                await database[collection].create_indexes([model])
            except OperationFailure as exc:
                logger.error(
                    "mongodb_index_failed",
                    collection=collection,
                    index=name,
                    code=exc.code,
                    codeName=(exc.details or {}).get("codeName"),
                )
                raise
    logger.info(
        "mongodb_indexes_ready",
        indexes={c: [m.document["name"] for m in ms] for c, ms in indexes.items()},
    )


async def ping(database: AsyncDatabase) -> bool:
    try:
        await database.client.admin.command("ping")
    except Exception:  # noqa: BLE001 — la sonda informa, no propaga
        return False
    return True


def get_db(request: Request) -> AsyncDatabase:
    return request.app.state.db
