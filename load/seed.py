"""Siembra y comprobación de la galería de un perfil 1:N (TFM-MIS-MEM/docs#62).

Corre DENTRO del contenedor `seed` de `compose.yml`: la imagen de `fr`, en la red
`internal` y en los vCPU de k6. Lo lanza `most.py` después de levantar el banco
y antes de la primera corrida del perfil; no está pensado para lanzarse a mano.

    python /load/seed.py seed  --faces N
    python /load/seed.py check --faces N --gallery G --identity I

LA SIEMBRA. Una galería `xs` y una identidad, por la API y con la cuenta de k6:
ésa es la que tiene que coincidir con la imagen de k6. El resto, hasta N rostros
exactos, directamente en MongoDB: un rostro por identidad, con códigos aleatorios
de 192 B. La búsqueda solo lee `faces`, así que la proporción entre rostros e
identidades no altera su coste.

  - Los documentos se construyen con las clases de persistencia del servicio
    (`src/documents`), las mismas que usa F4: el BSON sale con la forma que
    escribe la API, y la búsqueda corta los códigos por la vía rápida de
    `stored_code` y no por la de decodificar. La comprobación lo verifica.
  - Por lotes, en el orden de `createIdentity` (src/controllers/identities.py):
    identidades con `faceCount` 0, rostros, `$inc` de las identidades y `$inc`
    de la galería. Al final de cada lote se cumplen los invariantes.
  - Los códigos no respetan el código termómetro de LSSC, y da igual: Hamming
    cuesta lo mismo, y a ~768 ± 20 bits de cualquier sonda quedan muy lejos de
    `d_max` (479 en `xs`). Reproducibles: `random.Random(N)`.

LA COMPROBACIÓN, que bloquea: N rostros y N identidades, contadores coherentes,
la misma forma BSON en el rostro enrolado y en uno sembrado, y una búsqueda con
la imagen de k6 que devuelve 200 con la identidad enrolada. La salida es un JSON
en la última línea; el código de salida, 0 si todo se cumple.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid

from bson import ObjectId
from bson.raw_bson import RawBSONDocument
from pymongo import MongoClient

from src.documents.face import COLLECTION as FACES
from src.documents.face import FaceDocument
from src.documents.gallery import COLLECTION as GALLERIES
from src.documents.identity import COLLECTION as IDENTITIES
from src.documents.identity import IdentityDocument
from src.models.face import FaceSource

FR = "http://fr:8000/v1/fr/commercial"
# Las cabeceras de `k6/profile.js`: la misma cuenta.
HEADERS = {"X-Account-Id": "load-most", "X-Auth-Channel": "api", "X-Role": "CUSTOMER"}
ENROLLED = "/load/fixtures/koch_2023_480x640.jpg"
PROBE = "/load/fixtures/koch_2014_480x640.jpg"
MODEL = "xs"
CODE_BYTES = 192
BATCH = 5000

#: La condición de la vía rápida de `stored_code` (src/controllers/recognition.py):
#: el elemento binario `embedding`, de 192 B y subtipo genérico.
EMBEDDING = b"\x05embedding\x00"
CODE_HEADER = CODE_BYTES.to_bytes(4, "little") + b"\x00"


def db():
    return MongoClient(os.environ["MONGODB_URI"]).get_default_database()


def request(path: str, data: bytes, content_type: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        FR + path, data=data, method="POST", headers={**HEADERS, "Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def multipart(fields: dict[str, str], files: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = b""
    for name, value in fields.items():
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    for name, path in files.items():
        with open(path, "rb") as f:
            content = f.read()
        out += (
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{name}"; filename="{os.path.basename(path)}"\r\n'
                "Content-Type: image/jpeg\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def expect(status: int, body: dict, want: int, what: str) -> dict:
    if status != want:
        raise SystemExit(json.dumps({"ok": False, "problems": [f"{what}: {status} {body}"]}))
    return body


def seed(faces: int) -> dict:
    started = time.monotonic()
    body = json.dumps({"name": f"load-{faces}", "model": MODEL}).encode()
    gallery = expect(*request("/galleries", body, "application/json"), 201, "createGallery")
    gid = gallery["id"]
    data, ctype = multipart({"fullName": "koch-2023"}, {"image": ENROLLED})
    identity = expect(*request(f"/galleries/{gid}/identities", data, ctype), 201, "createIdentity")
    api_s = time.monotonic() - started

    database = db()
    gallery_oid = ObjectId(gid)
    rng = random.Random(faces)
    remaining = faces - 1
    done = 0
    while done < remaining:
        n = min(BATCH, remaining - done)
        ids = [ObjectId() for _ in range(n)]
        database[IDENTITIES].insert_many(
            [
                IdentityDocument(
                    id=oid, galleryId=gallery_oid, fullName=f"seed-{done + i + 1:06d}"
                ).to_document()
                for i, oid in enumerate(ids)
            ],
            ordered=False,
        )
        database[FACES].insert_many(
            [
                FaceDocument(
                    identityId=oid,
                    galleryId=gallery_oid,
                    embedding=rng.randbytes(CODE_BYTES),
                    isBinarized=True,
                    source=FaceSource.image,
                ).to_document()
                for oid in ids
            ],
            ordered=False,
        )
        database[IDENTITIES].update_many({"_id": {"$in": ids}}, {"$inc": {"faceCount": 1}})
        database[GALLERIES].update_one(
            {"_id": gallery_oid}, {"$inc": {"identityCount": n, "faceCount": n}}
        )
        done += n
    return {
        "gallery": gid,
        "identity": identity["id"],
        "faces": faces,
        "seconds": {
            "api": round(api_s, 2),
            "mongo": round(time.monotonic() - started - api_s, 2),
            "total": round(time.monotonic() - started, 2),
        },
    }


def fast_path(raw: bytes) -> bool:
    at = raw.find(EMBEDDING, 4) + len(EMBEDDING)
    return at >= len(EMBEDDING) and raw[at : at + 5] == CODE_HEADER


def check(faces: int, gid: str, iid: str) -> dict:
    database = db()
    gallery_oid, identity_oid = ObjectId(gid), ObjectId(iid)
    problems = []
    gallery = database[GALLERIES].find_one({"_id": gallery_oid}) or {}
    counts = {
        "faces": database[FACES].count_documents({"galleryId": gallery_oid}),
        "identities": database[IDENTITIES].count_documents({"galleryId": gallery_oid}),
        "identities_faceCount_not_1": database[IDENTITIES].count_documents(
            {"galleryId": gallery_oid, "faceCount": {"$ne": 1}}
        ),
        "gallery_faceCount": gallery.get("faceCount"),
        "gallery_identityCount": gallery.get("identityCount"),
        "gallery_status": gallery.get("status"),
        "gallery_model": gallery.get("model"),
    }
    for key in ("faces", "identities", "gallery_faceCount", "gallery_identityCount"):
        if counts[key] != faces:
            problems.append(f"{key} = {counts[key]}, se esperaban {faces}")
    if counts["identities_faceCount_not_1"]:
        problems.append(f"{counts['identities_faceCount_not_1']} identidades sin faceCount 1")
    if (counts["gallery_status"], counts["gallery_model"]) != ("active", MODEL):
        problems.append(f"galería {counts['gallery_status']} con {counts['gallery_model']}")

    shape = bson_shape(database, gallery_oid, identity_oid, faces, problems)

    # La búsqueda que hará k6, con su imagen y sin umbral.
    data, ctype = multipart({}, {"image": PROBE})
    started = time.monotonic()
    status, body = request(f"/galleries/{gid}/search", data, ctype)
    search = {"status": status, "ms": round((time.monotonic() - started) * 1000), "body": body}
    found = (body.get("identity") or {}).get("id")
    if status != 200 or found != iid:
        problems.append(f"la búsqueda no devuelve la identidad enrolada: {status} {found}")
    return {
        "ok": not problems,
        "problems": problems,
        "counts": counts,
        "shape": shape,
        "search": search,
    }


def bson_shape(
    database, gallery_oid: ObjectId, identity_oid: ObjectId, faces: int, problems: list[str]
) -> dict:
    """La forma BSON: el rostro de la API frente a uno sembrado."""
    raw = database.get_collection(FACES).with_options(
        codec_options=database.codec_options.with_options(document_class=RawBSONDocument)
    )
    enrolled = raw.find_one({"identityId": identity_oid})
    seeded = raw.find_one({"galleryId": gallery_oid, "identityId": {"$ne": identity_oid}})
    shape = {
        "enrolled_keys": list(enrolled.keys()) if enrolled else None,
        "seeded_keys": list(seeded.keys()) if seeded else None,
        "enrolled_fast_path": fast_path(enrolled.raw) if enrolled else None,
        "seeded_fast_path": fast_path(seeded.raw) if seeded else None,
    }
    if enrolled is None:
        problems.append("no está el rostro enrolado")
    elif faces > 1 and seeded is None:
        problems.append("no hay rostros sembrados")
    elif faces > 1 and shape["enrolled_keys"] != shape["seeded_keys"]:
        problems.append("el rostro sembrado no tiene la forma del enrolado")
    if enrolled is not None and not shape["enrolled_fast_path"]:
        problems.append("el rostro enrolado no cumple la vía rápida")
    if seeded is not None and not shape["seeded_fast_path"]:
        problems.append("el rostro sembrado no cumple la vía rápida")
    return shape


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("seed", "check"):
        p = sub.add_parser(name)
        p.add_argument("--faces", type=int, required=True)
        if name == "check":
            p.add_argument("--gallery", required=True)
            p.add_argument("--identity", required=True)
    args = parser.parse_args()
    if args.faces < 1:
        parser.error("--faces tiene que ser al menos 1")
    if args.cmd == "seed":
        print(json.dumps(seed(args.faces)))
        return
    result = check(args.faces, args.gallery, args.identity)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
