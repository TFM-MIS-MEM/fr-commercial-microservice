"""Identidad tal como sale por la API, y los cuerpos de entrada (F4, #36).

CERRADOS (`extra="forbid"`), como todo modelo de `src/models`.

`externalId` EN LA RESPUESTA: el contrato lo declara `nullable`, y el modelo lo
publica así (`str | None`), porque la comparación estática exige el mismo
esquema. Pero el servicio NUNCA emite `null`: guarda `externalId` ausente cuando
no se da (src/documents/identity.py) y lo omite, campo a campo, como
`updatedAt`. Omitir es conforme tanto con «cadena» como con «cadena o nulo».

EN LA ENTRADA, `null` no es «ausente», como en `GalleryUpdate`: ningún campo de
estos cuerpos declara `nullable`, así que el tipo es `str` con valor por omisión
`None`, que Pydantic no valida, y un `null` explícito falla como cadena. Lo
enviado se lee de `model_fields_set`. Consecuencia del contrato: un `externalId`
dado no se puede quitar con `updateIdentity`.

`externalId` exige `minLength: 1` y la cadena vacía es `400`, también en
multipart, donde una parte en blanco llega como `""` (medido: FastAPI 0.141.1 la
entrega al modelo, que la rechaza). No se normaliza a ausente: el porqué, en
`tests/contract/test_uniqueness.py`.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.json_schema import SkipJsonSchema

if TYPE_CHECKING:
    from src.documents.identity import IdentityDocument


OMIT_IF_ABSENT = ("externalId", "updatedAt")


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    galleryId: str  # noqa: N815
    fullName: str  # noqa: N815
    externalId: str | None = None  # noqa: N815
    faceCount: int  # noqa: N815
    createdAt: datetime  # noqa: N815
    updatedAt: datetime | SkipJsonSchema[None] = None  # noqa: N815

    # Sin anotación de retorno: src/models/gallery.py y CLAUDE.md.
    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for name in OMIT_IF_ABSENT:
            if data.get(name) is None:
                data.pop(name, None)
        return data

    @classmethod
    def from_document(cls, stored: "IdentityDocument") -> "Identity":
        """Campo a campo: la marca de borrado y lo que no se nombre no salen."""
        return cls(
            id=str(stored.id),
            galleryId=str(stored.galleryId),
            fullName=stored.fullName,
            externalId=stored.externalId,
            faceCount=stored.faceCount,
            createdAt=stored.createdAt,
            updatedAt=stored.updatedAt,
        )


class IdentityPage(BaseModel):
    """`200` de `listIdentities`: el mismo esquema en línea que `GalleryPage`."""

    model_config = ConfigDict(extra="forbid")

    items: list[Identity] | SkipJsonSchema[None] = None
    page: int | SkipJsonSchema[None] = None
    pageSize: int | SkipJsonSchema[None] = None  # noqa: N815
    total: int | SkipJsonSchema[None] = None


# ------------------------------------------------------------------ entrada


class CreateIdentityForm(BaseModel):
    """Cuerpo de `createIdentity`, cerrado y anotado con `File()` en el router.

    Solo `fullName` es obligatoria. Imagen o vector: las entradas por vector se
    rechazan en el controlador mientras F7 no verifique el token.
    """

    model_config = ConfigDict(extra="forbid")

    fullName: str  # noqa: N815
    externalId: str = Field(None, min_length=1)  # noqa: N815
    image: UploadFile | None = None
    embedding: str | None = None
    embeddingToken: str | None = None  # noqa: N815


class IdentityUpdate(BaseModel):
    """Cuerpo de `updateIdentity`. Sin `faceCount` ni `galleryId`: el cierre los rechaza."""

    model_config = ConfigDict(extra="forbid")

    fullName: str = None  # noqa: N815
    externalId: str = Field(None, min_length=1)  # noqa: N815
