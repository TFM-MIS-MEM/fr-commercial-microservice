"""Galería tal como se guarda en `galleries`.

INVARIANTE VALIDADO AL LEER, Y ES UNA DECISIÓN: `frozenReason` está presente si y
solo si `status` es `frozen`, que es lo que dice el contrato. Un documento que lo
incumple ---congelado sin motivo, o activo con motivo--- no se construye, y la
lectura acaba en `500`. La alternativa, servirlo, daría una respuesta que el
contrato declara imposible; corregirlo al vuelo sería ajustar en silencio un
estado que alguien escribió mal. Lo prueba `tests/persistence/test_documents.py`
en los dos sentidos, de extremo a extremo por `getGallery`.

`model`, `status` y `frozenReason` usan los enums de `src/models/gallery.py`: sus
valores son los del contrato, y se definen una sola vez.
"""

from datetime import datetime
from typing import ClassVar

from pydantic import Field, model_validator
from pymongo import ASCENDING, IndexModel

from src.documents.base import Document, utc_now
from src.models.gallery import FrozenReason, GalleryStatus, ModelName

COLLECTION = "galleries"

INDEXES = [
    # Único, y COMPARACIÓN EXACTA: sin intercalación, a propósito y declarado en
    # `ConflictGalleryName`. Sirve además las consultas por `accountId` solo
    # ---es prefijo del compuesto, medido en #34: `IXSCAN(accountId_1_name_1)`---,
    # por eso no hay índice `{accountId}` suelto.
    IndexModel([("accountId", ASCENDING), ("name", ASCENDING)], unique=True),
]

CONFLICTS = {"accountId_1_name_1": "GALLERY_NAME_ALREADY_EXISTS"}


class GalleryDocument(Document):
    OMIT_IF_ABSENT: ClassVar[tuple[str, ...]] = ("description", "frozenReason", "updatedAt")

    accountId: str  # noqa: N815
    name: str
    description: str | None = None
    model: ModelName
    status: GalleryStatus = GalleryStatus.active
    frozenReason: FrozenReason | None = None  # noqa: N815
    # Desnormalizados; los mantienen F3 y F4. El límite de rostros es de
    # ESTADO: se evalúa contra el valor actual, no contra un count().
    identityCount: int = 0  # noqa: N815
    faceCount: int = 0  # noqa: N815
    createdAt: datetime = Field(default_factory=utc_now)  # noqa: N815
    updatedAt: datetime | None = None  # noqa: N815

    @model_validator(mode="after")
    def _frozen_reason_iff_frozen(self) -> "GalleryDocument":
        frozen = self.status is GalleryStatus.frozen
        if frozen != (self.frozenReason is not None):
            raise ValueError(
                "frozenReason tiene que estar presente si y solo si status es frozen "
                f"(status={self.status.value}, frozenReason={self.frozenReason})"
            )
        return self
