"""`stored_code`: el código cortado del BSON crudo es el que daría decodificarlo.

La búsqueda 1:N no decodifica los documentos de `faces` (src/controllers/recognition.py).
Estas pruebas atan el corte a la decodificación de pymongo, y la vuelta a ella
cuando el elemento no tiene la forma de un código.
"""

import os

import bson
import pytest
from bson import ObjectId
from bson.raw_bson import RawBSONDocument

from src.controllers.recognition import stored_code
from src.inference import lssc


def raw(doc: dict) -> RawBSONDocument:
    return RawBSONDocument(bson.encode(doc))


@pytest.mark.parametrize("order", ["como la proyección", "código primero"])
def test_the_cut_is_the_decoded_code(order):
    code = os.urandom(lssc.CODE_BYTES)
    fields = {"_id": ObjectId(), "identityId": ObjectId(), "embedding": code}
    if order == "código primero":
        fields = {"embedding": code, "_id": fields["_id"], "identityId": fields["identityId"]}

    face = raw(fields)

    assert stored_code(face) == code == face["embedding"]


def test_a_decoy_before_the_element_falls_back_to_decoding():
    # Un valor anterior que contiene el nombre del elemento, sin su cabecera: el
    # primer acierto no es el código y se decodifica el documento.
    code = os.urandom(lssc.CODE_BYTES)
    face = raw({"_id": ObjectId(), "note": "\x05embedding\x00", "embedding": code})

    assert stored_code(face) == code


def test_a_code_of_another_length_falls_back_to_decoding():
    face = raw({"_id": ObjectId(), "embedding": b"\x01" * 10})

    assert stored_code(face) == b"\x01" * 10
