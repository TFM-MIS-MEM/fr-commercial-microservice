"""Identidad tal como se guarda en `identities`.

`faceCount` EMPIEZA EN 0, y es una decisión para F4. Hay un solo camino que
mantiene el contador: un `$inc` en cada alta y baja de rostro, incluido el
primero. Con 0 el contador nunca dice más rostros de los que hay, SIEMPRE QUE el
`$inc` vaya DESPUÉS de insertar el rostro. Si el proceso cae entre medias, queda
una identidad huérfana con `faceCount` 0 ---el contrato dice que una identidad no
existe sin rostro---, y la reconciliación la encuentra con una consulta:
`{faceCount: 0}`. Crearla con 1 obligaría a escribir la misma regla dos veces.

El MongoDB de este servicio es un nodo suelto, sin transacciones. Un replica set
de un nodo las habilitaría y haría atómicos la identidad y su primer rostro; eso
lo decide F4.

`externalId` se guarda AUSENTE cuando no hay, nunca `null`, y nunca vacío: el
contrato exige `minLength: 1` y aquí se rechaza también, sin normalizar `""` a
ausente. El porqué es el índice parcial de abajo.
"""

from datetime import datetime
from typing import ClassVar

from bson import ObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from src.documents.base import Document, utc_now

COLLECTION = "identities"

INDEXES = [
    # NO es redundante con el compuesto de abajo. Aquél es PARCIAL, y el
    # planificador solo usa un índice parcial si la consulta incluye su
    # predicado: `{galleryId}` a secas, o `{galleryId, externalId: "E-1"}` sin
    # `$type: "string"`, hacen COLLSCAN (medido en #34). El listado de una
    # galería necesita éste.
    IndexModel([("galleryId", ASCENDING)]),
    # Único y PARCIAL, no disperso. Un disperso compuesto indexa el documento en
    # cuanto está UNO de sus campos, y `galleryId` está siempre: la segunda
    # identidad sin `externalId` de una galería chocaba con la primera, y lo
    # mismo con `null` (medido en #34 contra mongod 7.0.40). El parcial deja
    # fuera todo lo que no es cadena.
    IndexModel(
        [("galleryId", ASCENDING), ("externalId", ASCENDING)],
        unique=True,
        partialFilterExpression={"externalId": {"$type": "string"}},
    ),
]

CONFLICTS = {"galleryId_1_externalId_1": "EXTERNAL_ID_ALREADY_EXISTS"}


class IdentityDocument(Document):
    OMIT_IF_ABSENT: ClassVar[tuple[str, ...]] = ("externalId", "updatedAt")

    galleryId: ObjectId  # noqa: N815
    fullName: str  # noqa: N815
    externalId: str | None = Field(default=None, min_length=1)  # noqa: N815
    faceCount: int = 0  # noqa: N815
    createdAt: datetime = Field(default_factory=utc_now)  # noqa: N815
    updatedAt: datetime | None = None  # noqa: N815
