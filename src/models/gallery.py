"""Galería tal como sale por la API.

Es el modelo de RESPUESTA de `components.schemas.Gallery`, no el de persistencia,
que vive en `src/documents/gallery.py` (#34).

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
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.json_schema import SkipJsonSchema

if TYPE_CHECKING:
    # Solo para el tipo: `src/documents` importa los enums de este módulo.
    from src.core.plan import Plan
    from src.documents.gallery import GalleryDocument


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
    def from_document(cls, stored: "GalleryDocument", plan: "Plan | None" = None) -> "Gallery":
        """Del documento ya leído (`src/documents`) a la respuesta, campo a campo.

        Nunca `cls(**stored.model_dump())`: el documento lleva `accountId`, y lo
        que no se nombra aquí no sale.

        CONGELADA AL VUELO (F10): con el plan de la petición, una galería cuyo
        modelo el plan no incluye sale `frozen` con `MODEL_NOT_IN_PLAN`, aunque
        el documento diga `active`. Es lo mismo que deciden las altas, la
        verificación y la búsqueda (`identities.owned_gallery`); no se escribe,
        y vuelve a `active` en cuanto el plan incluye el modelo.
        """
        status, reason = stored.status, stored.frozenReason
        if (
            plan is not None
            and status == GalleryStatus.active
            and not plan.allows_model(stored.model.value)
        ):
            status, reason = GalleryStatus.frozen, FrozenReason.MODEL_NOT_IN_PLAN
        return cls(
            id=str(stored.id),
            name=stored.name,
            description=stored.description,
            model=stored.model,
            status=status,
            frozenReason=reason,
            identityCount=stored.identityCount,
            faceCount=stored.faceCount,
            createdAt=stored.createdAt,
            updatedAt=stored.updatedAt,
        )


# ------------------------------------------------------------------ entrada
#
# `null` NO es «ausente». Ningún campo de estos cuerpos declara `nullable`, así
# que `"name": null` es un `400`. Por eso el tipo es `str` con valor por omisión
# `None`, y no `str | None`: Pydantic no valida el valor por omisión, de modo que
# un campo AUSENTE toma `None` sin error, y un `null` EXPLÍCITO falla como cadena.
# Lo que se envió se lee de `model_fields_set`, nunca comparando con `None`.
#
# `name` admite `""`: el contrato no declara `minLength` (decidido en F3, #35;
# la propuesta de cambio de contrato es un issue aparte con `alcance:futuro`).


class GalleryCreate(BaseModel):
    """Cuerpo de `createGallery`. Sin `status`: lo determina el sistema."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=120)
    description: str = Field(None, max_length=500)
    model: ModelName = None


class GalleryUpdate(BaseModel):
    """Cuerpo de `updateGallery`. Sin `model` ni `status`: el cierre los rechaza."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(None, max_length=120)
    description: str = Field(None, max_length=500)


# ------------------------------------------------------------------ listado


class GalleryPage(BaseModel):
    """`200` de `listGalleries`.

    El esquema en línea del contrato no declara `required`, así que los cuatro
    campos se publican opcionales; el servicio los envía SIEMPRE. Ninguno admite
    `null`: `SkipJsonSchema` quita esa rama del esquema publicado, igual que en
    `Gallery`.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[Gallery] | SkipJsonSchema[None] = None
    page: int | SkipJsonSchema[None] = None
    pageSize: int | SkipJsonSchema[None] = None  # noqa: N815
    total: int | SkipJsonSchema[None] = None
