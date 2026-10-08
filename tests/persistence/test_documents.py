"""Modelos de persistencia: `src/documents` (docs#34).

No son esquemas del contrato. Lo que se prueba es lo que deciden ellos:

  - Valores iniciales: contadores a 0, `status` `active`, `createdAt` con zona.
  - Lo opcional se guarda AUSENTE, nunca `null`. En `externalId` es además lo
    que mantiene a la identidad fuera del índice único parcial.
  - Al leer, los campos de más se ignoran.
  - El invariante de `frozenReason` se valida al leer: un documento corrupto da
    `500`, en los dos sentidos.
"""

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.documents.face import FaceDocument, FaceSource
from src.documents.gallery import GalleryDocument
from src.documents.identity import IdentityDocument
from src.main import create_app
from src.models.gallery import FrozenReason, GalleryStatus, ModelName
from tests.conftest import BASE_URL
from tests.support.identity import gateway_headers
from tests.support.seed import seed_gallery
from tests.support.settings import settings

GALLERY_ID = ObjectId("66f0000000000000000000aa")
IDENTITY_ID = ObjectId("66f0000000000000000000cc")


# ---------------------------------------------------------------- galerías


def test_a_new_gallery_starts_active_with_zero_counters_and_a_utc_timestamp():
    gallery = GalleryDocument(accountId="acc-a", name="Empleados", model=ModelName.s)

    assert gallery.status is GalleryStatus.active
    assert (gallery.identityCount, gallery.faceCount) == (0, 0)
    assert gallery.createdAt.tzinfo is timezone.utc


def test_a_new_gallery_is_written_without_absent_optionals_nor_id():
    doc = GalleryDocument(accountId="acc-a", name="Empleados", model=ModelName.s).to_document()

    assert doc == {
        "accountId": "acc-a",
        "name": "Empleados",
        "model": "s",
        "status": "active",
        "identityCount": 0,
        "faceCount": 0,
        "createdAt": doc["createdAt"],
    }


def test_reading_ignores_fields_it_does_not_know():
    stored = {
        "_id": GALLERY_ID,
        "accountId": "acc-a",
        "name": "Empleados",
        "model": "s",
        "status": "active",
        "identityCount": 3,
        "faceCount": 7,
        "createdAt": datetime(2026, 9, 10, tzinfo=timezone.utc),
        "internalNote": "no se modela",
    }

    gallery = GalleryDocument.model_validate(stored)

    assert gallery.id == GALLERY_ID
    assert "internalNote" not in gallery.to_document()


CORRUPT = {
    "frozen sin motivo": {"status": "frozen"},
    "active con motivo": {"status": "active", "frozenReason": "MODEL_NOT_IN_PLAN"},
}


@pytest.mark.parametrize("fields", CORRUPT.values(), ids=CORRUPT.keys())
def test_frozen_reason_is_present_if_and_only_if_the_gallery_is_frozen(fields):
    with pytest.raises(ValidationError, match="frozenReason"):
        GalleryDocument(accountId="acc-a", name="Empleados", model=ModelName.s, **fields)


def test_a_frozen_gallery_with_its_reason_is_valid():
    gallery = GalleryDocument(
        accountId="acc-a",
        name="Empleados",
        model=ModelName.base,
        status=GalleryStatus.frozen,
        frozenReason=FrozenReason.MODEL_NOT_IN_PLAN,
    )

    assert gallery.to_document()["frozenReason"] == "MODEL_NOT_IN_PLAN"


@pytest.mark.parametrize("fields", CORRUPT.values(), ids=CORRUPT.keys())
def test_a_corrupt_gallery_is_a_500_when_read(test_db_uri, db, fields):
    # Decisión escrita en src/documents/gallery.py: un documento que incumple el
    # invariante no se sirve. Mejor un 500 que una respuesta que el contrato
    # declara imposible. Cliente propio: el común relanza la excepción en vez de
    # devolver la respuesta que el cliente vería.
    app = create_app(settings(test_db_uri))
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
        gallery_id = seed_gallery(db, "acc-a", **fields)
        res = client.get(
            f"/v1/fr/commercial/galleries/{gallery_id}", headers=gateway_headers("acc-a")
        )

    assert res.status_code == 500
    assert res.json() == {"code": "INTERNAL_ERROR", "message": "Internal server error"}


# ------------------------------------------------------------- identidades


def test_a_new_identity_starts_with_zero_faces_and_no_external_id():
    identity = IdentityDocument(galleryId=GALLERY_ID, fullName="Ana")
    doc = identity.to_document()

    assert identity.faceCount == 0
    assert doc == {
        "galleryId": GALLERY_ID,
        "fullName": "Ana",
        "faceCount": 0,
        "createdAt": doc["createdAt"],
    }


def test_an_explicit_null_external_id_is_still_written_absent():
    doc = IdentityDocument(galleryId=GALLERY_ID, fullName="Ana", externalId=None).to_document()

    assert "externalId" not in doc


def test_an_empty_external_id_is_rejected_not_normalised():
    with pytest.raises(ValidationError, match="externalId"):
        IdentityDocument(galleryId=GALLERY_ID, fullName="Ana", externalId="")


# ------------------------------------------------------------------ rostros


def test_a_face_carries_its_gallery_and_no_update_timestamp():
    face = FaceDocument(
        identityId=IDENTITY_ID,
        galleryId=GALLERY_ID,
        embedding=b"\x00" * 64,
        isBinarized=True,
        source=FaceSource.image,
    )
    doc = face.to_document()

    assert doc == {
        "identityId": IDENTITY_ID,
        "galleryId": GALLERY_ID,
        "embedding": b"\x00" * 64,
        "isBinarized": True,
        "source": "image",
        "createdAt": doc["createdAt"],
    }
    assert doc["createdAt"].tzinfo is timezone.utc
