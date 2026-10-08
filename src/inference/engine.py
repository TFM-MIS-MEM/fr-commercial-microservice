"""El motor de inferencia: MTCNN y los tres EdgeFace, cargados una vez.

Se carga en el `lifespan`, después de los índices (`src/main.py`). Si falta un
fichero de pesos, o un fichero no casa con su arquitectura (`strict=True`), la
carga lanza y el servicio no arranca: uvicorn no acepta conexiones hasta que el
`lifespan` termina, así que `/health` no responde hasta que los modelos están
cargados.

TODO ES BLOQUEANTE. torch y numpy no ceden el bucle de eventos: el controlador
llama a estos métodos desde un hilo (`run_in_threadpool`) y bajo el semáforo de
`INFERENCE_CONCURRENCY` (src/controllers/inference.py).

LA VERSIÓN. `xs-v1`, `s-v1`, `base-v1` identifican el conjunto que hace
comparables dos vectores: pesos, detector, alineación, preprocesado y cortes
LSSC (src/inference/lssc.py). Cambiar cualquiera de ellos es una versión
nueva, nunca una actualización silenciosa (contrato, `extractEmbedding`).
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from src.inference import edgeface
from src.inference.mtcnn import MTCNN
from src.inference.mtcnn.detector import WEIGHTS as MTCNN_WEIGHTS

VERSION = "v1"
MODELS = ("xs", "s", "base")


def versioned(model: str) -> str:
    return f"{model}-{VERSION}"


@dataclass(frozen=True)
class Face:
    """Un rostro detectado, con la caja ya en píxeles enteros de la imagen."""

    x: int
    y: int
    width: int
    height: int
    confidence: float
    landmarks: np.ndarray


def _box(raw: np.ndarray, width: int, height: int) -> tuple[int, int, int, int]:
    """De (x1, y1, x2, y2) en coma flotante y coordenadas inclusivas ---MTCNN mide
    `w = x2 - x1 + 1`, y la caja puede salirse de la imagen--- a (x, y, ancho,
    alto) enteros, recortados a los bordes."""
    x1 = min(max(round(float(raw[0])), 0), width - 1)
    y1 = min(max(round(float(raw[1])), 0), height - 1)
    x2 = min(max(round(float(raw[2])), 0), width - 1)
    y2 = min(max(round(float(raw[3])), 0), height - 1)
    return x1, y1, x2 - x1 + 1, y2 - y1 + 1


def _preprocess(aligned: Image.Image) -> torch.Tensor:
    """`ToTensor()` + `Normalize([0.5]*3, [0.5]*3)`, como `preprocess_image` de
    fr-compilation, sin torchvision. Su `Resize((112, 112))` no hace nada: el
    recorte alineado ya mide 112 × 112."""
    array = np.asarray(aligned, dtype=np.float32) / 255
    array = (array - 0.5) / 0.5
    return torch.from_numpy(array.transpose(2, 0, 1).copy()).unsqueeze(0)


class Engine:
    def __init__(self, checkpoints: Path):
        expected = [*MTCNN_WEIGHTS.values(), *edgeface.WEIGHTS.values()]
        missing = [name for name in expected if not (checkpoints / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Faltan pesos en {checkpoints}: {', '.join(missing)}")

        self.detector = MTCNN(checkpoints)
        self.models: dict[str, torch.nn.Module] = {}
        for name in MODELS:
            model = edgeface.BUILDERS[name]()
            state = torch.load(
                checkpoints / edgeface.WEIGHTS[name], map_location="cpu", weights_only=True
            )
            model.load_state_dict(state, strict=True)
            model.eval()
            self.models[name] = model

    def detect(self, image: Image.Image) -> list[Face]:
        """Todos los rostros, ordenados por confianza de mayor a menor."""
        boxes, landmarks = self.detector.detect(image)
        width, height = image.size
        faces = [
            Face(*_box(boxes[i], width, height), float(boxes[i][4]), landmarks[i])
            for i in range(len(boxes))
        ]
        return sorted(faces, key=lambda face: face.confidence, reverse=True)

    def align(self, image: Image.Image, face: Face) -> Image.Image:
        return self.detector.align(np.array(image), face.landmarks)

    def embed(self, model: str, aligned: Image.Image) -> np.ndarray:
        """El vector de 512 dimensiones, SIN normalizar (src/inference/lssc.py)."""
        with torch.no_grad():
            return self.models[model](_preprocess(aligned))[0].numpy()


@cache
def load_engine(checkpoints: Path) -> Engine:
    """Una carga por proceso y directorio.

    En producción el `lifespan` corre una vez y la caché no cambia nada. En las
    pruebas, cada `TestClient` ejecuta su `lifespan`: sin ella, cada prueba
    volvería a cargar los cuatro modelos.
    """
    return Engine(checkpoints)
