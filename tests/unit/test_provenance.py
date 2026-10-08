"""Token de procedencia: determinista y atado a cada uno de sus datos."""

import pytest

from src.inference import provenance

BASE = {"secret": "s3cret", "model": "xs-v1", "binarized": True, "embedding": b"\x01\x02\x03"}


def sign(**overrides):
    args = {**BASE, **overrides}
    return provenance.sign(args["secret"], args["model"], args["binarized"], args["embedding"])


def test_the_same_vector_gives_the_same_token():
    assert sign() == sign()


def test_the_format_is_version_payload_signature_without_padding():
    version, payload, signature = sign().split(".")

    assert version == "v1"
    assert "=" not in payload + signature


@pytest.mark.parametrize(
    "change",
    [
        {"secret": "other"},
        {"model": "s-v1"},
        {"binarized": False},
        {"embedding": b"\x01\x02\x04"},
    ],
)
def test_changing_anything_changes_the_token(change):
    assert sign(**change) != sign()
