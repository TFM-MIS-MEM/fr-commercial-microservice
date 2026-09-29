"""Siembra directa en MongoDB, con el cliente síncrono.

Los documentos tienen la forma que tendrán en `galleries`, incluido `accountId`,
que es justo lo que la respuesta NO debe devolver.
"""

from datetime import datetime, timezone

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
