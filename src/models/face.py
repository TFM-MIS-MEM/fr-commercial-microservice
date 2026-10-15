"""Rostro tal como sale por la API, y el cuerpo de `addFace` (F4, #36).

Es el modelo de `components.schemas.Face`, cerrado como él. NO lleva el
`embedding`: el contrato no lo declara en la respuesta, y el código guardado
(F6, #38) no sale nunca del servicio por esta vía.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from src.documents.face import FaceDocument


class FaceSource(str, Enum):
    image = "image"
    embedding = "embedding"


class Face(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    identityId: str  # noqa: N815
    galleryId: str  # noqa: N815
    isBinarized: bool  # noqa: N815
    source: FaceSource
    createdAt: datetime  # noqa: N815

    @classmethod
    def from_document(cls, stored: "FaceDocument") -> "Face":
        """Campo a campo: lo que no se nombra ---el `embedding`--- no sale."""
        return cls(
            id=str(stored.id),
            identityId=str(stored.identityId),
            galleryId=str(stored.galleryId),
            isBinarized=stored.isBinarized,
            source=stored.source,
            createdAt=stored.createdAt,
        )


class AddFaceForm(BaseModel):
    """Cuerpo de `addFace`, cerrado y anotado con `File()` en el router.

    El contrato no declara ninguna parte obligatoria: imagen o vector. Las
    entradas por vector se rechazan en el controlador mientras F7 no verifique
    el token, y sin ninguna de las dos es `400`.
    """

    model_config = ConfigDict(extra="forbid")

    image: UploadFile | None = None
    embedding: str | None = None
    embeddingToken: str | None = None  # noqa: N815
