"""Base común de los documentos."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field

#: MARCA DE BORRADO de una cascada en curso, en `galleries` y en `identities`.
#: Es un campo interno: los modelos de persistencia lo ignoran al leer y no está
#: en ningún modelo de la API. La ponen `deleteGallery` y `deleteIdentity` antes
#: de borrar nada, y las altas de F4 filtran por `LIVE` en su `$inc`: así una
#: alta que se cruza con la cascada o queda dentro de lo que ésta borra o se
#: deshace. El razonamiento completo, en `src/controllers/galleries.delete_gallery`.
DELETING = "deletingAt"

#: Lo que no está marcado para borrarse.
LIVE = {DELETING: {"$exists": False}}


def utc_now() -> datetime:
    """Ahora, en UTC y TRUNCADO A MILISEGUNDOS, que es lo que guarda MongoDB.

    Sin truncar, la respuesta de una creación ---que se construye con el
    documento en memoria--- llevaría microsegundos, y la lectura posterior del
    mismo recurso no: dos `createdAt` distintos para el mismo instante (#35).
    """
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


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
