"""Rostro tal como se guarda en `faces`.

`galleryId` está replicado a propósito: resuelve la búsqueda 1:N con una sola
consulta proyectada sobre esta colección, sin un `$in` de miles de identidades.
Un rostro no se modifica, así que no lleva `updatedAt`.

`embedding` existe y es binario; su formato ---binarizado, 64 B--- es de F6.
"""

from datetime import datetime
from enum import Enum

from bson import ObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from src.documents.base import Document, utc_now

COLLECTION = "faces"

INDEXES = [
    # Crítico para 1:N: el cuello de botella es recuperar los vectores, no
    # inferir.
    IndexModel([("galleryId", ASCENDING)]),
    # Borrado en cascada de una identidad y `deleteFace`.
    IndexModel([("identityId", ASCENDING)]),
]

CONFLICTS: dict[str, str] = {}


class FaceSource(str, Enum):
    """`Face.source` del contrato. Vive aquí hasta que F4 cree el modelo de API."""

    image = "image"
    embedding = "embedding"


class FaceDocument(Document):
    identityId: ObjectId  # noqa: N815
    galleryId: ObjectId  # noqa: N815
    embedding: bytes
    isBinarized: bool  # noqa: N815
    source: FaceSource
    createdAt: datetime = Field(default_factory=utc_now)  # noqa: N815
