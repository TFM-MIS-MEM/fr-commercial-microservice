"""detectFaces, verifyPair y extractEmbedding, contra el contrato (F5, #37).

TODA RESPUESTA PASA POR EL ARNÉS, como en `test_get_gallery.py`: `post()` la
valida contra el contrato y, si es un error que no es de dominio, exige además
la forma desnuda.

INFERENCIA REAL. Las imágenes son tres retratos de dominio público de la NASA
(`tests/fixtures/faces/SOURCES.md`): Christina Koch en 2014 y en 2023, y Sunita
Williams. Las pruebas de verificación son las que atan los cortes y los
umbrales de `src/inference/lssc.py` al comportamiento: la misma persona por
encima del umbral de cada modelo y personas distintas por debajo.

EL CIERRE DEL CUERPO MULTIPART se prueba aquí, con peticiones: la comparación
estática lo deja fuera con su motivo (`test_shape.py`, `WITHOUT_BODY`). Cada
rechazo va emparejado con su petición legítima, que da `200`.
"""

import base64
import hashlib
import hmac
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.inference import lssc
from tests.contract.harness import assert_bare_error, assert_satisfies_contract
from tests.support.identity import gateway_headers
from tests.support.settings import TOKEN_SECRET

DETECTION = "/v1/fr/commercial/detection"
VERIFICATION = "/v1/fr/commercial/verification"
EXTRACTION = "/v1/fr/commercial/extract-embeddings"

FACES = Path(__file__).resolve().parents[1] / "fixtures" / "faces"
KOCH_2014 = (FACES / "koch_2014.jpg").read_bytes()
KOCH_2023 = (FACES / "koch_2023.jpg").read_bytes()
WILLIAMS = (FACES / "williams_2018.jpg").read_bytes()

MODELS = ("xs", "s", "base")
DEFAULT_THRESHOLD = {"xs": 1 - 479 / 1536, "s": 1 - 466 / 1536, "base": 1 - 520 / 1536}

WEB = gateway_headers(channel="frontend")
API = gateway_headers(channel="api")


def post(client, path, files=None, data=None, headers=WEB):
    res = client.post(path, files=files, data=data, headers=headers)
    assert_satisfies_contract(res)
    if res.status_code >= 400 and not res.json().get("details"):
        assert_bare_error(res)
    return res


def jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def blank(width=200, height=200) -> bytes:
    """Una imagen válida sin rostro."""
    return png(Image.new("RGB", (width, height), (128, 128, 128)))


def side_by_side(*images: bytes) -> bytes:
    """Dos retratos en una sola imagen: dos rostros."""
    decoded = [Image.open(io.BytesIO(data)).convert("RGB") for data in images]
    canvas = Image.new("RGB", (sum(i.width for i in decoded), max(i.height for i in decoded)))
    x = 0
    for image in decoded:
        canvas.paste(image, (x, 0))
        x += image.width
    return jpeg(canvas)


def image_part(data: bytes, name="face.jpg"):
    return (name, data, "application/octet-stream")


def assert_error(res, status, code):
    assert res.status_code == status, res.text
    assert res.json()["code"] == code, res.text


# =================================================================== detectFaces


def test_detect_200_one_face_without_crop(client):
    res = post(client, DETECTION, files={"image": image_part(KOCH_2023)})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["numFaces"] == 1
    (face,) = body["faces"]
    assert set(face) == {"boundingBox", "confidence"}
    box = face["boundingBox"]
    assert 0 <= box["x"] and box["x"] + box["width"] <= 240
    assert 0 <= box["y"] and box["y"] + box["height"] <= 320
    assert 0.9 < face["confidence"] <= 1


def test_detect_200_with_crops_is_the_aligned_112_png(client):
    res = post(
        client, DETECTION, files={"image": image_part(KOCH_2023)}, data={"includeCrops": "true"}
    )

    assert res.status_code == 200, res.text
    crop = Image.open(io.BytesIO(base64.b64decode(res.json()["faces"][0]["crop"])))
    assert crop.format == "PNG"
    assert crop.size == (112, 112)


def test_detect_200_two_faces_ordered_by_confidence(client):
    data = side_by_side(KOCH_2023, WILLIAMS)

    res = post(client, DETECTION, files={"image": image_part(data)})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["numFaces"] == 2
    confidences = [face["confidence"] for face in body["faces"]]
    assert confidences == sorted(confidences, reverse=True)


def test_detect_only_first_is_the_most_confident(client):
    data = side_by_side(KOCH_2023, WILLIAMS)
    every = post(client, DETECTION, files={"image": image_part(data)}).json()

    res = post(client, DETECTION, files={"image": image_part(data)}, data={"onlyFirst": "true"})

    assert res.status_code == 200, res.text
    assert res.json() == {"faces": every["faces"][:1], "numFaces": 1}


@pytest.mark.parametrize("only_first", ["false", "true"])
def test_detect_200_empty_when_there_is_no_face(client, only_first):
    res = post(
        client, DETECTION, files={"image": image_part(blank())}, data={"onlyFirst": only_first}
    )

    assert res.status_code == 200, res.text
    assert res.json() == {"faces": [], "numFaces": 0}


def test_detect_400_a_part_the_contract_does_not_declare(client):
    legit = post(client, DETECTION, files={"image": image_part(KOCH_2023)})
    assert legit.status_code == 200

    res = post(
        client, DETECTION, files={"image": image_part(KOCH_2023)}, data={"accountId": "other"}
    )

    assert_error(res, 400, "BAD_REQUEST")


def test_detect_400_model_is_no_longer_a_part(client):
    # Se quitó del contrato en #37: el detector es el mismo para todos.
    res = post(client, DETECTION, files={"image": image_part(KOCH_2023)}, data={"model": "xs"})

    assert_error(res, 400, "BAD_REQUEST")


def test_detect_400_without_image(client):
    res = post(client, DETECTION, data={"onlyFirst": "true"})

    assert_error(res, 400, "BAD_REQUEST")


def test_detect_400_bytes_that_are_not_an_image(client):
    res = post(client, DETECTION, files={"image": image_part(b"not an image at all")})

    assert_error(res, 400, "INVALID_IMAGE")


def test_detect_400_a_truncated_image(client):
    res = post(client, DETECTION, files={"image": image_part(KOCH_2023[:2000])})

    assert_error(res, 400, "INVALID_IMAGE")


@pytest.mark.parametrize("size", [(4097, 1), (1, 4097)])
def test_detect_400_beyond_4096_pixels_on_a_side(client, size):
    res = post(client, DETECTION, files={"image": image_part(blank(*size))})

    assert_error(res, 400, "IMAGE_TOO_LARGE")


@pytest.mark.parametrize("size", [(4096, 1), (1, 4096)])
def test_detect_200_at_exactly_4096_pixels(client, size):
    res = post(client, DETECTION, files={"image": image_part(blank(*size))})

    assert res.status_code == 200, res.text


# ==================================================================== verifyPair


def verify(client, image1=KOCH_2014, image2=KOCH_2023, data=None):
    files = {"image1": image_part(image1), "image2": image_part(image2)}
    return post(client, VERIFICATION, files=files, data=data)


@pytest.mark.parametrize("model", MODELS)
def test_verify_200_same_person(client, model):
    res = verify(client, data={"model": model})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["isTheSame"] is True
    assert body["model"] == f"{model}-v1"
    assert body["thresholdUsed"] == pytest.approx(DEFAULT_THRESHOLD[model])
    assert body["confidence"] >= body["thresholdUsed"]


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("other", ["KOCH_2014", "KOCH_2023"])
def test_verify_200_different_people(client, model, other):
    res = verify(client, image1=globals()[other], image2=WILLIAMS, data={"model": model})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["isTheSame"] is False
    assert body["confidence"] < body["thresholdUsed"]


def test_verify_without_model_uses_xs(client):
    res = verify(client)

    assert res.json()["model"] == "xs-v1"


def test_verify_the_requested_threshold_decides(client):
    genuine = verify(client, data={"threshold": "1"}).json()
    impostor = verify(client, image2=WILLIAMS, data={"threshold": "0"}).json()

    assert (genuine["isTheSame"], genuine["thresholdUsed"]) == (False, 1)
    assert (impostor["isTheSame"], impostor["thresholdUsed"]) == (True, 0)


@pytest.mark.parametrize("threshold", ["-0.1", "1.5", "high"])
def test_verify_400_threshold_out_of_range(client, threshold):
    res = verify(client, data={"threshold": threshold})

    assert_error(res, 400, "BAD_REQUEST")


@pytest.mark.parametrize("part", ["embedding1", "embeddingToken1", "embedding2", "embeddingToken2"])
def test_verify_400_vector_inputs_are_not_supported(client, part):
    res = verify(client, data={part: "AAAA"})

    assert_error(res, 400, "EMBEDDING_INPUT_NOT_SUPPORTED")
    assert res.json()["details"] == {"parts": [part]}


@pytest.mark.parametrize("missing", ["image1", "image2"])
def test_verify_400_both_images_are_required(client, missing):
    files = {"image1": image_part(KOCH_2014), "image2": image_part(KOCH_2023)}
    del files[missing]

    res = post(client, VERIFICATION, files=files)

    assert_error(res, 400, "BAD_REQUEST")


@pytest.mark.parametrize("part", ["image1", "image2"])
def test_verify_400_no_face_says_in_which_image(client, part):
    images = {"image1": KOCH_2014, "image2": KOCH_2023, part: blank()}

    res = verify(client, **images)

    assert_error(res, 400, "NO_FACE_DETECTED")
    assert res.json()["details"] == {"part": part}


def test_verify_400_a_part_the_contract_does_not_declare(client):
    res = verify(client, data={"gallery": "x"})

    assert_error(res, 400, "BAD_REQUEST")


def test_verify_confidence_is_one_minus_hamming_over_1536(client):
    def code(data):
        body = post(client, EXTRACTION, files={"image": image_part(data)}, headers=API).json()
        return base64.b64decode(body["embedding"])

    distance = lssc.hamming(code(KOCH_2014), code(KOCH_2023))

    assert verify(client).json()["confidence"] == pytest.approx(1 - distance / 1536)


# ============================================================== extractEmbedding


def extract(client, image=KOCH_2023, data=None, headers=API):
    return post(client, EXTRACTION, files={"image": image_part(image)}, data=data, headers=headers)


def token_parts(token: str):
    version, payload, signature = token.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    return version, json.loads(base64.urlsafe_b64decode(padded)), payload, signature


@pytest.mark.parametrize("model", MODELS)
def test_extract_200_binarized_by_default(client, model):
    res = extract(client, data={"model": model})

    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["model"], body["isBinarized"]) == (f"{model}-v1", True)
    code = base64.b64decode(body["embedding"])
    assert len(code) == 192  # 512 dimensiones × 3 bits


def test_extract_without_model_uses_xs(client):
    assert extract(client).json()["model"] == "xs-v1"


def test_extract_200_real_vector_is_unnormalised_and_is_what_gets_binarized(client):
    real = extract(client, data={"returnBinarized": "false"}).json()
    binarized = extract(client).json()

    vector = np.frombuffer(base64.b64decode(real["embedding"]), dtype="<f4")
    assert real["isBinarized"] is False
    assert vector.shape == (512,)
    assert not np.isclose(np.linalg.norm(vector), 1, atol=1e-3)
    assert lssc.binarize(vector, "xs") == base64.b64decode(binarized["embedding"])


@pytest.mark.parametrize("binarized", ["true", "false"])
def test_extract_the_token_signs_model_representation_and_vector(client, binarized):
    body = extract(client, data={"returnBinarized": binarized}).json()

    version, claims, payload, signature = token_parts(body["embeddingToken"])
    raw = base64.b64decode(body["embedding"])
    expected = hmac.new(
        TOKEN_SECRET.encode(), f"{version}.{payload}".encode(), hashlib.sha256
    ).digest()
    assert version == "v1"
    assert claims == {
        "binarized": binarized == "true",
        "model": "xs-v1",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected


def test_extract_401_from_the_web_channel(client):
    legit = extract(client)
    assert legit.status_code == 200

    res = extract(client, headers=WEB)

    assert_error(res, 401, "API_CHANNEL_REQUIRED")


def test_extract_400_no_face(client):
    res = extract(client, image=blank())

    assert_error(res, 400, "NO_FACE_DETECTED")
    assert res.json()["details"] == {"part": "image"}


def test_extract_400_a_part_the_contract_does_not_declare(client):
    res = extract(client, data={"threshold": "0.5"})

    assert_error(res, 400, "BAD_REQUEST")


def test_extract_400_unknown_model(client):
    res = extract(client, data={"model": "xxs"})

    assert_error(res, 400, "BAD_REQUEST")
