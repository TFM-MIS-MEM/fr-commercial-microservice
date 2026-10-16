"""Carreras sin transacciones (F4, #36): entrelazados FORZADOS, no probabilísticos.

Dos peticiones reales contra la misma aplicación, como dos tareas en el bucle de
eventos del `TestClient` (su portal, donde corrió el `lifespan` y vive el
cliente de MongoDB). Cada una se detiene en una escritura concreta con un
gancho sobre `AsyncCollection` que se dispara UNA SOLA VEZ y solo en SU
colección: las llamadas del otro controlador ---incluidas las compensaciones---
pasan por la misma clase y no deben detenerse ni soltar a nadie.

LAS VENTANAS QUE SE FUERZAN, y lo que cierra cada una:

  1. Alta contra la cascada de `deleteGallery`. El alta ha leído la galería
     ANTES de la marca de borrado y se detiene en su primera inserción; la
     cascada marca, borra rostros (y, según el caso, identidades) y se detiene
     antes de su último paso; el alta termina; la cascada termina. El alta
     inserta DESPUÉS de los `delete_many`: solo la salvan el filtro de la marca
     en su `$inc` sobre la galería y la compensación. Sin marca, sin filtro o
     sin compensación quedan datos biométricos de una galería borrada.
  2. `addFace` contra `deleteIdentity`: lo mismo con la marca de la identidad.
  3. Dos `deleteFace` sobre una identidad de dos rostros: el primero ha leído
     su rostro y se detiene JUSTO ANTES de escribir en la identidad; el segundo
     corre entero. Con una lectura previa del contador en lugar del decremento
     condicional, el primero decidiría con un valor caducado y borrarían los
     dos. Detenerlo después del decremento no discrimina: medido en la sesión
     16, un mutante con lectura previa pasaba así en verde.

Resultado esperado de 1 y 2: `404` del alta, `204` de la cascada, y nada de lo
que el alta insertó en la base.
"""

import asyncio

import httpx2
from bson import ObjectId
from pymongo.asynchronous.collection import AsyncCollection

from tests.conftest import BASE_URL
from tests.contract.test_inference import KOCH_2023, image_part
from tests.support.galleries import ONE as GALLERY
from tests.support.identities import (
    FACE,
    FACES,
    IDENTITIES,
    IDENTITY,
    assert_not_found,
    checked,
    faces_of,
    identity_doc,
)
from tests.support.identity import ACCOUNT_A, gateway_headers
from tests.support.seed import seed_gallery, seed_identity_with_faces

#: Incluye una inferencia; solo se agota si un entrelazado no llega a su pausa.
TIMEOUT_S = 60


class Pause:
    """Detiene la PRIMERA llamada a `AsyncCollection.<method>` sobre `collection`."""

    def __init__(self, monkeypatch, collection: str, method: str):
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        original = getattr(AsyncCollection, method)
        fired = False

        async def paused(coll, *args, **kwargs):
            nonlocal fired
            if coll.name == collection and not fired:
                fired = True
                self.reached.set()
                await self.release.wait()
            return await original(coll, *args, **kwargs)

        monkeypatch.setattr(AsyncCollection, method, paused)


def interleave(client, first, first_pause, second, second_pause=None):
    """`first` se detiene; `second` arranca y se detiene (o termina); `first`
    termina; `second` termina. Devuelve las dos respuestas."""

    async def run():
        transport = httpx2.ASGITransport(app=client.app)
        async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            async with asyncio.timeout(TIMEOUT_S):
                a = asyncio.create_task(first(http))
                await first_pause.reached.wait()
                b = asyncio.create_task(second(http))
                if second_pause is not None:
                    await second_pause.reached.wait()
                else:
                    await asyncio.wait({b})
                first_pause.release.set()
                res_a = await a
                if second_pause is not None:
                    second_pause.release.set()
                res_b = await b
            # El arnés lee el cuerpo de la petición, y el cliente asíncrono deja
            # el multipart en flujo, sin leer.
            for res in (res_a, res_b):
                await res.request.aread()
        return res_a, res_b

    res_a, res_b = client.portal.call(run)
    return checked(res_a), checked(res_b)


def enroll_identity(gallery_id):
    def send(http):
        return http.post(
            IDENTITIES.format(gallery_id),
            data={"fullName": "Eva"},
            files={"image": image_part(KOCH_2023)},
            headers=gateway_headers(),
        )

    return send


def add_face(gallery_id, identity_id):
    def send(http):
        return http.post(
            FACES.format(gallery_id, identity_id),
            files={"image": image_part(KOCH_2023)},
            headers=gateway_headers(),
        )

    return send


def delete(url):
    def send(http):
        return http.delete(url, headers=gateway_headers())

    return send


def in_gallery(db, gallery_id):
    oid = ObjectId(gallery_id)
    return (
        db.galleries.count_documents({"_id": oid}),
        db.identities.count_documents({"galleryId": oid}),
        db.faces.count_documents({"galleryId": oid}),
    )


# ------------------------------------------------- 1. alta contra deleteGallery


def test_createIdentity_racing_deleteGallery_leaves_nothing(client, db, monkeypatch):
    gallery = seed_gallery(db, ACCOUNT_A, model="xs")
    seed_identity_with_faces(db, gallery, faces=2)
    enrolling = Pause(monkeypatch, "identities", "insert_one")
    deleting = Pause(monkeypatch, "galleries", "delete_one")

    enrolled, deleted = interleave(
        client, enroll_identity(gallery), enrolling, delete(GALLERY.format(gallery)), deleting
    )

    assert_not_found(enrolled, "Gallery")
    assert deleted.status_code == 204, deleted.text
    assert in_gallery(db, gallery) == (0, 0, 0)


def test_addFace_racing_deleteGallery_leaves_nothing(client, db, monkeypatch):
    # La cascada se detiene tras borrar los rostros y antes de borrar las
    # identidades: la identidad aún existe, y lo que decide es la galería.
    gallery = seed_gallery(db, ACCOUNT_A, model="xs")
    identity = seed_identity_with_faces(db, gallery, faces=1)
    enrolling = Pause(monkeypatch, "faces", "insert_one")
    deleting = Pause(monkeypatch, "identities", "delete_many")

    enrolled, deleted = interleave(
        client, add_face(gallery, identity), enrolling, delete(GALLERY.format(gallery)), deleting
    )

    assert_not_found(enrolled, "Gallery")
    assert deleted.status_code == 204, deleted.text
    assert in_gallery(db, gallery) == (0, 0, 0)


# ------------------------------------------------ 2. addFace contra deleteIdentity


def test_addFace_racing_deleteIdentity_leaves_nothing(client, db, monkeypatch):
    gallery = seed_gallery(db, ACCOUNT_A, model="xs", identityCount=2, faceCount=3)
    identity = seed_identity_with_faces(db, gallery, faces=1)
    neighbour = seed_identity_with_faces(db, gallery, faces=2)
    enrolling = Pause(monkeypatch, "faces", "insert_one")
    deleting = Pause(monkeypatch, "identities", "delete_one")

    enrolled, deleted = interleave(
        client,
        add_face(gallery, identity),
        enrolling,
        delete(IDENTITY.format(gallery, identity)),
        deleting,
    )

    assert_not_found(enrolled, "Identity")
    assert deleted.status_code == 204, deleted.text
    assert identity_doc(db, identity) is None
    assert faces_of(db, identity) == []
    assert len(faces_of(db, neighbour)) == 2


# ------------------------------------------------ 3. dos deleteFace concurrentes


def test_two_concurrent_deleteFace_leave_the_identity_with_one_face(client, db, monkeypatch):
    gallery = seed_gallery(db, ACCOUNT_A, identityCount=1, faceCount=2)
    identity = seed_identity_with_faces(db, gallery, faces=2)
    first, second = [str(f["_id"]) for f in faces_of(db, identity)]
    discounting = Pause(monkeypatch, "identities", "update_one")

    res_first, res_second = interleave(
        client,
        delete(FACE.format(gallery, identity, first)),
        discounting,
        delete(FACE.format(gallery, identity, second)),
    )

    assert res_second.status_code == 204, res_second.text
    assert res_first.status_code == 409, res_first.text
    assert res_first.json()["code"] == "LAST_FACE_OF_IDENTITY"
    assert [str(f["_id"]) for f in faces_of(db, identity)] == [first]
    assert identity_doc(db, identity)["faceCount"] == 1
