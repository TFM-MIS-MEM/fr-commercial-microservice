"""Decodificación de las imágenes que llegan en las operaciones de inferencia.

EL TOPE DE PÍXELES SE COMPRUEBA ANTES DE DECODIFICAR. `Image.open` lee solo la
cabecera; las dimensiones salen de ahí, y `convert` es lo que decodifica. Por
encima de 4096 píxeles de ancho o de alto se rechaza sin haber reservado la
memoria de la imagen: el coste de MTCNN crece con los píxeles (su pirámide de
escalas), y una imagen grande es el vector de denegación de servicio más barato
de estas operaciones.

Lo que NO se acota aquí: los bytes del cuerpo, que FastAPI ya ha leído enteros
antes de llegar a este punto, y que van a la pasarela (TFM-MIS-MEM/docs#116).
Ni la orientación EXIF (docs#118) ni el formato (docs#119): la imagen se
decodifica como viene, con cualquier formato que lea Pillow, igual que en
fr-compilation.
"""

import io

from PIL import Image, UnidentifiedImageError

from src.core.errors import ErrorReportError

MAX_SIDE = 4096


def _invalid() -> ErrorReportError:
    return ErrorReportError(400, "INVALID_IMAGE", "The image could not be decoded")


def _too_large() -> ErrorReportError:
    return ErrorReportError(
        400, "IMAGE_TOO_LARGE", f"The image exceeds {MAX_SIDE} pixels in width or height"
    )


def open_image(data: bytes) -> Image.Image:
    """La imagen en RGB, o `400` con `INVALID_IMAGE` o `IMAGE_TOO_LARGE`."""
    try:
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        # Pillow corta antes que nosotros por encima de ~179 Mpx, y toda imagen
        # así supera los 4096 píxeles en algún lado.
        raise _too_large() from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise _invalid() from None

    width, height = image.size
    if width > MAX_SIDE or height > MAX_SIDE:
        raise _too_large()

    try:
        return image.convert("RGB")
    except (OSError, ValueError):
        # Cabecera válida y datos truncados o corruptos.
        raise _invalid() from None
