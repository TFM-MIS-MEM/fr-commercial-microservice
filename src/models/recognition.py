"""Modelos de la inferencia con estado: `verifyAgainstIdentity` y `searchGallery` (F8, #40).

LOS CUERPOS son `multipart/form-data` y se cierran como los de F4 y F5: un
modelo `extra="forbid"` anotado con `File()` en el router (src/models/__init__.py).

LA RESPUESTA DE `verifyAgainstIdentity` es `VerificationResult`, la de
`verifyPair` (src/models/inference.py).

LA DE `searchGallery`, cerrada y con solo dos campos, sin `thresholdUsed` ni
`model`, que el contrato no declara:

  - `identity`, OBLIGATORIA Y ANULABLE: `Identity | None` sin valor por omisión,
    para que FastAPI la publique en `required`. Sin coincidencia sale `null`,
    nunca ausente. Es el «X o nulo» que `tests/contract/shape.py` traduce desde
    el `anyOf: [Identity, NullValue]` del contrato (#40).
  - `confidence`, solo con coincidencia: se omite por nombre (`OMIT_IF_ABSENT`),
    nunca con `exclude_none` global, que quitaría también el `null` de
    `identity`. Las cotas, dentro del tipo, como `thresholdUsed` de
    `VerificationResult`.
"""

from typing import Annotated

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.json_schema import SkipJsonSchema

from src.models.identity import Identity
from src.models.inference import FLOAT

# --------------------------------------------------------------- peticiones


class VerifyAgainstIdentityForm(BaseModel):
    """Imagen o vector, y `livenessToken`. Los vectores y `livenessToken` se
    rechazan en el controlador, con su código, hasta F7 y F12."""

    model_config = ConfigDict(extra="forbid")

    image: UploadFile | None = None
    embedding: str | None = None
    embeddingToken: str | None = None  # noqa: N815
    threshold: float | None = Field(default=None, ge=0, le=1)
    livenessToken: str | None = None  # noqa: N815


class SearchGalleryForm(BaseModel):
    """Imagen o vector. Sin `livenessToken`: el contrato no lo declara aquí, y
    el cierre lo rechaza como cualquier otra parte de más."""

    model_config = ConfigDict(extra="forbid")

    image: UploadFile | None = None
    embedding: str | None = None
    embeddingToken: str | None = None  # noqa: N815
    threshold: float | None = Field(default=None, ge=0, le=1)


# --------------------------------------------------------------- respuestas


OMIT_IF_ABSENT = ("confidence",)


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: Identity | None
    confidence: Annotated[float, Field(ge=0, le=1)] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=FLOAT
    )

    # Sin anotación de retorno: src/models/gallery.py y CLAUDE.md.
    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for name in OMIT_IF_ABSENT:
            if data.get(name) is None:
                data.pop(name, None)
        return data
