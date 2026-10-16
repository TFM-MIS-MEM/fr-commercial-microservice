"""Llamadas a `verifyAgainstIdentity` y `searchGallery`, y siembra de rostros (F8, #40).

Toda respuesta pasa por el arnés (`tests/support/identities.checked`).

LOS CÓDIGOS SEMBRADOS se construyen a una distancia EXACTA del de la imagen de
la petición: `probe_code` es el código que da `extractEmbedding` para esa imagen
con ese modelo ---el mismo camino que usa el servicio, igual byte a byte desde
F4---, y `at_distance` invierte exactamente `d` bits. Así las fronteras
(`d_max` y `d_max + 1`) y los empates se prueban sin depender de lo que se
parezcan dos fotografías.
"""

import numpy as np
from bson import ObjectId

from tests.contract.test_inference import KOCH_2023, image_part
from tests.support.identities import IDENTITY, checked, extracted_code
from tests.support.identity import gateway_headers
from tests.support.seed import CREATED

VERIFY = IDENTITY + "/verify"
SEARCH = "/v1/fr/commercial/galleries/{}/search"


def _post(client, path, data, image, headers, files):
    if files is None:
        files = {"image": image_part(image)} if image is not None else None
    return checked(client.post(path, data=data, files=files, headers=headers or gateway_headers()))


def verify(client, gallery_id, identity_id, data=None, image=KOCH_2023, headers=None, files=None):
    """`verifyAgainstIdentity`. `image=None` no envía la parte; `files` la sustituye."""
    return _post(client, VERIFY.format(gallery_id, identity_id), data, image, headers, files)


def search(client, gallery_id, data=None, image=KOCH_2023, headers=None, files=None):
    """`searchGallery`. `image=None` no envía la parte; `files` la sustituye."""
    return _post(client, SEARCH.format(gallery_id), data, image, headers, files)


def probe_code(client, model, image=KOCH_2023) -> bytes:
    """El código LSSC de la imagen de la petición con ese modelo."""
    return extracted_code(client, image, model)


def at_distance(code: bytes, d: int) -> bytes:
    """Un código a distancia de Hamming exactamente `d` de `code`."""
    bits = np.unpackbits(np.frombuffer(code, dtype=np.uint8))
    bits[:d] ^= 1
    return np.packbits(bits).tobytes()


def mark_deleting(collection, doc_id: str) -> None:
    """La marca de un borrado en curso o caído (`src/documents/base.DELETING`)."""
    collection.update_one({"_id": ObjectId(doc_id)}, {"$set": {"deletingAt": CREATED}})


def seed_identity(db, gallery_id, codes, face_ids=None, **overrides) -> str:
    """Una identidad de la galería con un rostro por código, como los dejaría F4.

    `face_ids` fija el `_id` de cada rostro, para los empates.
    """
    gallery = ObjectId(gallery_id)
    identity = db.identities.insert_one(
        {
            "galleryId": gallery,
            "fullName": "Ana",
            "faceCount": len(codes),
            "createdAt": CREATED,
            **overrides,
        }
    ).inserted_id
    for i, code in enumerate(codes):
        face = {
            "identityId": identity,
            "galleryId": gallery,
            "embedding": code,
            "isBinarized": True,
            "source": "image",
            "createdAt": CREATED,
        }
        if face_ids is not None:
            face["_id"] = face_ids[i]
        db.faces.insert_one(face)
    return str(identity)
