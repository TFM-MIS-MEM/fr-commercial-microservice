"""Base común de los documentos."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    #: Campos opcionales que se OMITEN al escribir si no tienen valor.
    OMIT_IF_ABSENT: ClassVar[tuple[str, ...]] = ()

    id: ObjectId | None = Field(default=None, alias="_id")

    def to_document(self) -> dict[str, Any]:
        """El documento tal como se escribe: sin `_id` si es nuevo, sin nulos."""
        doc = self.model_dump(mode="python", by_alias=True)
        for name in ("_id", *self.OMIT_IF_ABSENT):
            if doc.get(name) is None:
                doc.pop(name, None)
        # Los enums se guardan por su valor, que es lo que filtran las consultas.
        return {k: v.value if isinstance(v, Enum) else v for k, v in doc.items()}
