"""Modelos de las tres operaciones de inferencia sin estado (F5, #37).

LOS CUERPOS son `multipart/form-data` y se cierran con un modelo
`extra="forbid"` anotado con `File()` en el router (src/models/__init__.py):
una parte que el contrato no declara da `400`. FastAPI lee el cuerpo entero
antes de resolver la identidad; es el coste asumido en la sesión 9, y el tope de
bytes va a la pasarela (TFM-MIS-MEM/docs#116).

LAS RESPUESTAS, cerradas como sus esquemas. Donde el contrato no declara
`required` ---`DetectedFace` y su `boundingBox`; `thresholdUsed` y `model` de
`VerificationResult`---, el campo es opcional en el esquema publicado
(`SkipJsonSchema[None]`, el mismo recurso que `Gallery`), aunque el servicio
los rellene siempre. `crop`, en cambio, solo sale si se pidió, y se omite por
nombre (`OMIT_IF_ABSENT`), nunca con `exclude_none` global.

`format: float` se declara con `json_schema_extra` porque Pydantic no lo
escribe para `float` y la comparación estática lo exige.
"""

from typing import Annotated

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.json_schema import SkipJsonSchema

from src.models.gallery import ModelName

FLOAT = {"format": "float"}

# --------------------------------------------------------------- peticiones


class DetectFacesForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: UploadFile
    onlyFirst: bool = False  # noqa: N815
    includeCrops: bool = False  # noqa: N815


class VerifyPairForm(BaseModel):
    """El contrato no declara ninguna parte obligatoria: con imágenes o con
    vectores. Las entradas por vector se rechazan en el controlador, con su
    código, mientras F7 no verifique el token (contrato, `verifyPair`)."""

    model_config = ConfigDict(extra="forbid")

    image1: UploadFile | None = None
    embedding1: str | None = None
    embeddingToken1: str | None = None  # noqa: N815
    image2: UploadFile | None = None
    embedding2: str | None = None
    embeddingToken2: str | None = None  # noqa: N815
    model: ModelName = ModelName.xs
    threshold: float | None = Field(default=None, ge=0, le=1)


class ExtractEmbeddingForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: UploadFile
    model: ModelName = ModelName.xs
    returnBinarized: bool = True  # noqa: N815


# --------------------------------------------------------------- respuestas


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int | SkipJsonSchema[None] = None
    y: int | SkipJsonSchema[None] = None
    width: int | SkipJsonSchema[None] = None
    height: int | SkipJsonSchema[None] = None


OMIT_IF_ABSENT = ("crop",)


class DetectedFace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boundingBox: BoundingBox | SkipJsonSchema[None] = None  # noqa: N815
    confidence: float | SkipJsonSchema[None] = Field(default=None, json_schema_extra=FLOAT)
    crop: str | SkipJsonSchema[None] = Field(
        default=None, description="Recorte en base64, presente solo si se solicitó."
    )

    # Sin anotación de retorno: src/models/gallery.py y CLAUDE.md.
    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for name in OMIT_IF_ABSENT:
            if data.get(name) is None:
                data.pop(name, None)
        return data


class DetectFacesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    faces: list[DetectedFace]
    numFaces: int  # noqa: N815


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isTheSame: bool  # noqa: N815
    confidence: float = Field(ge=0, le=1, json_schema_extra=FLOAT)
    # Las cotas DENTRO del tipo: sobre la unión con `SkipJsonSchema[None]`,
    # Pydantic no las aplica y las publica como `ge`/`le` sueltas (la
    # comparación estática lo cazó).
    thresholdUsed: Annotated[float, Field(ge=0, le=1)] | SkipJsonSchema[None] = Field(  # noqa: N815
        default=None, json_schema_extra=FLOAT
    )
    model: str | SkipJsonSchema[None] = None


class ExtractEmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding: str = Field(description="Vector codificado en base64.")
    embeddingToken: str  # noqa: N815
    model: str
    isBinarized: bool  # noqa: N815
