"""Galería tal como sale por la API.

Es el modelo de RESPUESTA de `components.schemas.Gallery`, no el modelo de
dominio: índices, contadores y reconciliación son F2 (#34).

CERRADO (`extra="forbid"`), como `components.schemas.Gallery` desde docs#103:
el esquema publicado lleva `additionalProperties: false` y la comparación
estática lo exige.

OMISIÓN CAMPO A CAMPO, NO `exclude_none` GLOBAL. Tres campos del contrato son
opcionales y ninguno admite `null`:

  - `description` y `updatedAt`: `type: string`, sin `nullable`.
  - `frozenReason`: presente si y solo si `status` es `frozen`. Hasta docs#103
    el contrato lo declaraba `nullable` junto a un `allOf` y sin `type`, que en
    3.0.3 no permite `null` y en lo que dos validadores discrepaban; se decidió
    omitirlo y quitar el `nullable`. El cierre no cambia esto: prohíbe los
    nombres que no se declaran, no obliga a enviar los que sí.

La forma conforme es no emitirlos cuando no hay valor. Se hace nombrándolos uno
a uno en `OMIT_IF_ABSENT`, y no con `exclude_none` sobre toda la respuesta: eso
borraría también un `null` que el contrato SÍ declare en otro campo, y lo haría
sin que nadie lo decidiera.

`SkipJsonSchema[None]` quita la rama `null` del esquema que genera FastAPI, de
modo que la comparación con el contrato ve «cadena opcional» y no «cadena o
nulo», que sería falso.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_serializer
from pydantic.json_schema import SkipJsonSchema


class ModelName(str, Enum):
    xs = "xs"
    s = "s"
    base = "base"


class GalleryStatus(str, Enum):
    active = "active"
    frozen = "frozen"


class FrozenReason(str, Enum):
    MODEL_NOT_IN_PLAN = "MODEL_NOT_IN_PLAN"


OMIT_IF_ABSENT = ("description", "frozenReason", "updatedAt")


class Gallery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str | SkipJsonSchema[None] = None
    model: ModelName
    status: GalleryStatus
    frozenReason: FrozenReason | SkipJsonSchema[None] = None  # noqa: N815
    identityCount: int  # noqa: N815
    faceCount: int  # noqa: N815
    createdAt: datetime  # noqa: N815
    updatedAt: datetime | SkipJsonSchema[None] = None  # noqa: N815

    # SIN anotación de retorno, y no por descuido. Pydantic deriva de ella el
    # esquema de SERIALIZACIÓN, que es el que FastAPI publica para la respuesta:
    # con `-> Any` el esquema de Gallery generado era `{}`. Las respuestas seguían
    # saliendo bien; lo que se perdía era el esquema, y la comparación estática
    # (tests/contract/shape.py) lo cazó en su primera ejecución, en #33.
    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for name in OMIT_IF_ABSENT:
            if data.get(name) is None:
                data.pop(name, None)
        return data

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> "Gallery":
        """Del documento de MongoDB a la respuesta, campo a campo.

        Nunca `cls(**doc)`: el documento lleva `accountId`, y lo que no se nombra
        aquí no sale.
        """
        return cls(
            id=str(doc["_id"]),
            name=doc["name"],
            description=doc.get("description"),
            model=doc["model"],
            status=doc["status"],
            frozenReason=doc.get("frozenReason"),
            identityCount=doc["identityCount"],
            faceCount=doc["faceCount"],
            createdAt=doc["createdAt"],
            updatedAt=doc.get("updatedAt"),
        )
