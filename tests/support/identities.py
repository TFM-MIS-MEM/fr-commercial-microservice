"""Llamadas a las operaciones de identidades y rostros, TODAS a través del arnés.

`checked()` valida cada respuesta contra el contrato y, si es un error que no
lleva `details`, exige la forma desnuda (`code` y `message`). Los errores de
dominio con `details` ---`EMBEDDING_INPUT_NOT_SUPPORTED`, `NO_FACE_DETECTED`---
son los únicos que la llevan, como en `test_inference.py`.
"""

import base64

from bson import ObjectId

from tests.contract.harness import assert_bare_error, assert_satisfies_contract
from tests.contract.test_inference import KOCH_2014, image_part
from tests.support.identity import gateway_headers

IDENTITIES = "/v1/fr/commercial/galleries/{}/identities"
IDENTITY = IDENTITIES + "/{}"
FACES = IDENTITY + "/faces"
FACE = FACES + "/{}"
EXTRACTION = "/v1/fr/commercial/extract-embeddings"

#: Campos que llevan siempre; `externalId` y `updatedAt` se omiten si no hay.
IDENTITY_REQUIRED = {"id", "galleryId", "fullName", "faceCount", "createdAt"}
FACE_FIELDS = {"id", "identityId", "galleryId", "isBinarized", "source", "createdAt"}


def checked(response):
    assert_satisfies_contract(response)
    if response.status_code >= 400 and not response.json().get("details"):
        assert_bare_error(response)
    return response


def enroll(client, gallery_id, data=None, image=KOCH_2014, headers=None, files=None):
    """`createIdentity`. `image=None` no envía la parte; `files` la sustituye."""
    if files is None:
        files = {"image": image_part(image)} if image is not None else None
    data = {"fullName": "Ana"} if data is None else data
    return checked(
        client.post(
            IDENTITIES.format(gallery_id),
            data=data,
            files=files,
            headers=headers or gateway_headers(),
        )
    )


def listing(client, gallery_id, params=None, headers=None):
    return checked(
        client.get(
            IDENTITIES.format(gallery_id), params=params, headers=headers or gateway_headers()
        )
    )


def fetch(client, gallery_id, identity_id, headers=None):
    return checked(
        client.get(IDENTITY.format(gallery_id, identity_id), headers=headers or gateway_headers())
    )


def update(client, gallery_id, identity_id, body, headers=None):
    return checked(
        client.patch(
            IDENTITY.format(gallery_id, identity_id),
            json=body,
            headers=headers or gateway_headers(),
        )
    )


def delete(client, gallery_id, identity_id, headers=None):
    return checked(
        client.delete(
            IDENTITY.format(gallery_id, identity_id), headers=headers or gateway_headers()
        )
    )


def add_face(client, gallery_id, identity_id, data=None, image=KOCH_2014, headers=None, files=None):
    if files is None:
        files = {"image": image_part(image)} if image is not None else None
    return checked(
        client.post(
            FACES.format(gallery_id, identity_id),
            data=data,
            files=files,
            headers=headers or gateway_headers(),
        )
    )


def delete_face(client, gallery_id, identity_id, face_id, headers=None):
    return checked(
        client.delete(
            FACE.format(gallery_id, identity_id, face_id), headers=headers or gateway_headers()
        )
    )


def extracted_code(client, image, model) -> bytes:
    """El código LSSC que `extractEmbedding` da para esa imagen y ese modelo."""
    res = checked(
        client.post(
            EXTRACTION,
            data={"model": model},
            files={"image": image_part(image)},
            headers=gateway_headers(channel="api"),
        )
    )
    assert res.status_code == 200, res.text
    return base64.b64decode(res.json()["embedding"])


# ------------------------------------------------------------------ aserciones


def assert_error(res, status, code, message=None):
    assert res.status_code == status, res.text
    assert res.json()["code"] == code, res.text
    if message is not None:
        assert res.json()["message"] == message, res.text


def assert_not_found(res, resource):
    assert res.status_code == 404, res.text
    assert res.json() == {"code": "NOT_FOUND", "message": f"{resource} not found"}


def assert_bad_request(res):
    assert res.status_code == 400, res.text
    assert res.json() == {"code": "BAD_REQUEST", "message": "Invalid request"}


# ------------------------------------------------------------------ la base


def state(db, gallery_id):
    """Todo lo que importa de una galería en la base: documentos y contadores.

    `(identidades, rostros, identityCount, faceCount)`; la galería borrada da
    `(…, None, None)`.
    """
    oid = ObjectId(gallery_id)
    gallery = db.galleries.find_one({"_id": oid}) or {}
    return (
        db.identities.count_documents({"galleryId": oid}),
        db.faces.count_documents({"galleryId": oid}),
        gallery.get("identityCount"),
        gallery.get("faceCount"),
    )


def identity_doc(db, identity_id):
    return db.identities.find_one({"_id": ObjectId(identity_id)})


def faces_of(db, identity_id):
    return list(db.faces.find({"identityId": ObjectId(identity_id)}).sort("_id", 1))
