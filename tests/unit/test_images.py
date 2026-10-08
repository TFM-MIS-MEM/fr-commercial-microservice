"""La rama de `open_image` que las pruebas de contrato no alcanzan.

Pillow corta con `DecompressionBombError` por encima de ~179 Mpx, en el propio
`Image.open` y antes de la comprobación de 4096 píxeles. Fabricar una cabecera
así es posible pero frágil; se rebaja el límite de Pillow solo para esta
prueba.
"""

import io

import pytest
from PIL import Image

from src.core.errors import ErrorReportError
from src.core.images import open_image


def test_a_decompression_bomb_is_image_too_large(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buffer, format="PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)

    with pytest.raises(ErrorReportError) as caught:
        open_image(buffer.getvalue())

    assert (caught.value.status_code, caught.value.code) == (400, "IMAGE_TOO_LARGE")
