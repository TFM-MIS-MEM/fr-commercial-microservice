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
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

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


async def ping(database: AsyncDatabase) -> bool:
    try:
        await database.client.admin.command("ping")
    except Exception:  # noqa: BLE001 — la sonda informa, no propaga
        return False
    return True


def get_db(request: Request) -> AsyncDatabase:
    return request.app.state.db
