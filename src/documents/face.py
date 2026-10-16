"""Rostro tal como se guarda en `faces`.

`galleryId` está replicado a propósito: resuelve la búsqueda 1:N con una sola
consulta proyectada sobre esta colección, sin un `$in` de miles de identidades.
Un rostro no se modifica, así que no lleva `updatedAt`.

`embedding` (F6, #38) es el CÓDIGO LSSC del vector, 192 B, calculado con el
modelo de la galería (`src/inference/lssc.py`): 512 dimensiones × 3 bits, frente
a 2 KB en float32. Es el formato nativo del índice: la búsqueda 1:N (F8) compara
por distancia de Hamming sin reconstruir el vector. `isBinarized` es siempre
`true` en lo que guarda F4; el campo existe porque el contrato prevé enrolar
desde un vector (F7). `source` dice de dónde salió: hoy, siempre `image`.
"""

from datetime import datetime

from bson import ObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from src.documents.base import Document, utc_now
from src.models.face import FaceSource

COLLECTION = "faces"

INDEXES = [
    # Crítico para 1:N: el cuello de botella es recuperar los vectores, no
    # inferir.
    IndexModel([("galleryId", ASCENDING)]),
    # Borrado en cascada de una identidad y `deleteFace`.
    IndexModel([("identityId", ASCENDING)]),
]

CONFLICTS: dict[str, str] = {}


class FaceDocument(Document):
    identityId: ObjectId  # noqa: N815
    galleryId: ObjectId  # noqa: N815
    embedding: bytes
    isBinarized: bool  # noqa: N815
    source: FaceSource
    createdAt: datetime = Field(default_factory=utc_now)  # noqa: N815
