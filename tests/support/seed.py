"""Siembra directa en MongoDB, con el cliente síncrono.

Los documentos tienen la forma que tendrán en `galleries`, incluido `accountId`,
que es justo lo que la respuesta NO debe devolver.
"""

from datetime import datetime, timezone

from bson import ObjectId

CREATED = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)


def seed_gallery(db, account_id: str, **overrides) -> str:
    doc = {
        "accountId": account_id,
        "name": "Empleados",
        "model": "s",
        "status": "active",
        "identityCount": 3,
        "faceCount": 7,
        "createdAt": CREATED,
        **overrides,
    }
    return str(db.galleries.insert_one(doc).inserted_id)


def seed_identity_with_faces(db, gallery_id: str, faces: int = 2) -> str:
    """Una identidad de la galería con `faces` rostros, como los dejaría F4."""
    gallery = ObjectId(gallery_id)
    identity = db.identities.insert_one(
        {"galleryId": gallery, "fullName": "Ana", "faceCount": faces, "createdAt": CREATED}
    ).inserted_id
    for _ in range(faces):
        db.faces.insert_one(
            {
                "identityId": identity,
                "galleryId": gallery,
                "embedding": b"\x00" * 192,
                "isBinarized": True,
                "source": "image",
                "createdAt": CREATED,
            }
        )
    return str(identity)
