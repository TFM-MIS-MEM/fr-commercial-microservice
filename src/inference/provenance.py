"""Token de procedencia de un vector (`embeddingToken` de `extractEmbedding`).

Un HMAC-SHA256, con `EMBEDDING_TOKEN_SECRET`, sobre el modelo con su versión,
la representación (binarizada o no) y el SHA-256 de los bytes exactos del
vector que se devuelve. La representación va dentro porque los mismos bytes
significan cosas distintas en una y otra.

Formato: `v1.<carga>.<firma>`, ambas en base64url sin relleno; la carga es JSON
compacto con las claves ordenadas, de modo que el mismo vector da siempre el
mismo token.

NADIE LO VERIFICA TODAVÍA. La verificación al enrolar desde vector es F7
(TFM-MIS-MEM/docs#39), en `alcance:futuro`; mientras tanto, las operaciones que
admitirían vectores los rechazan (`EMBEDDING_INPUT_NOT_SUPPORTED`).
"""

import base64
import hashlib
import hmac
import json

FORMAT = "v1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign(secret: str, model: str, binarized: bool, embedding: bytes) -> str:
    payload = json.dumps(
        {"binarized": binarized, "model": model, "sha256": hashlib.sha256(embedding).hexdigest()},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signed = f"{FORMAT}.{_b64(payload)}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
    return f"{signed.decode('ascii')}.{_b64(signature)}"
