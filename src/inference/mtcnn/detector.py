"""Detector y alineador MTCNN.

Copiado de fr-compilation, `face_alignment/mtcnn.py` (AdaFace, MIT:
`LICENSE-AdaFace`). Cambios, todos sin efecto sobre el resultado:

  - Los pesos se leen de `checkpoints/mtcnn_*.npz` con `allow_pickle=False`,
    por ruta absoluta. El original hacía `os.chdir` al directorio del módulo
    para que `get_nets.py` encontrara sus `.npy` por ruta relativa: un cambio de
    directorio de trabajo de todo el proceso, que no es seguro con hilos.
  - Siempre en CPU: sin el parámetro `device` ni su `assert`.
  - `align_multi` se parte en `detect` y `align`, porque `detectFaces` necesita
    las cajas de todos los rostros y solo alinea los que va a devolver. Ni
    `limit` ni la selección de rostro viven aquí: el original se quedaba con el
    primero que devolvía el NMS (`limit=1`); el servicio, con el de mayor
    confianza (`src/inference/engine.py`).

Los parámetros del detector son los del original: `min_face_size=20`,
umbrales `[0.6, 0.7, 0.9]`, NMS `[0.7, 0.7, 0.7]` y factor de escala `0.85`.
"""

from pathlib import Path

import numpy as np
import torch
from PIL import Image

from src.inference.mtcnn.align import get_square_reference_facial_points, warp_and_crop_face
from src.inference.mtcnn.box_utils import calibrate_box, convert_to_square, get_image_boxes, nms
from src.inference.mtcnn.first_stage import run_first_stage
from src.inference.mtcnn.nets import ONet, PNet, RNet

CROP_SIZE = (112, 112)
WEIGHTS = {"pnet": "mtcnn_pnet.npz", "rnet": "mtcnn_rnet.npz", "onet": "mtcnn_onet.npz"}


def _weights(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


class MTCNN:
    def __init__(self, checkpoints: Path):
        self.pnet = PNet(_weights(checkpoints / WEIGHTS["pnet"]))
        self.rnet = RNet(_weights(checkpoints / WEIGHTS["rnet"]))
        self.onet = ONet(_weights(checkpoints / WEIGHTS["onet"]))
        self.pnet.eval()
        self.rnet.eval()
        self.onet.eval()
        self.refrence = get_square_reference_facial_points()

        self.min_face_size = 20
        self.thresholds = [0.6, 0.7, 0.9]
        self.nms_thresholds = [0.7, 0.7, 0.7]
        self.factor = 0.85

    def detect(self, img: Image.Image):
        """Cajas `[n, 5]` (x1, y1, x2, y2, confianza) y puntos `[n, 10]`."""
        return self.detect_faces(
            img, self.min_face_size, self.thresholds, self.nms_thresholds, self.factor
        )

    def align(self, img_array: np.ndarray, landmark) -> Image.Image:
        """El recorte alineado de 112 × 112 de un rostro, como en `align_multi`."""
        facial5points = [[landmark[j], landmark[j + 5]] for j in range(5)]
        warped_face = warp_and_crop_face(img_array, facial5points, self.refrence, CROP_SIZE)
        return Image.fromarray(warped_face)

    def detect_faces(self, image, min_face_size, thresholds, nms_thresholds, factor):
        """
        Arguments:
            image: an instance of PIL.Image.
            min_face_size: a float number.
            thresholds: a list of length 3.
            nms_thresholds: a list of length 3.

        Returns:
            two float numpy arrays of shapes [n_boxes, 4] and [n_boxes, 10],
            bounding boxes and facial landmarks.
        """

        # BUILD AN IMAGE PYRAMID
        width, height = image.size
        min_length = min(height, width)

        min_detection_size = 12

        # scales for scaling the image
        scales = []

        # scales the image so that
        # minimum size that we can detect equals to
        # minimum face size that we want to detect
        m = min_detection_size / min_face_size
        min_length *= m

        factor_count = 0
        while min_length > min_detection_size:
            scales.append(m * factor**factor_count)
            min_length *= factor
            factor_count += 1

        # STAGE 1

        # it will be returned
        bounding_boxes = []

        with torch.no_grad():
            # run P-Net on different scales
            for s in scales:
                boxes = run_first_stage(image, self.pnet, scale=s, threshold=thresholds[0])
                bounding_boxes.append(boxes)

            # collect boxes (and offsets, and scores) from different scales
            bounding_boxes = [i for i in bounding_boxes if i is not None]
            if len(bounding_boxes) == 0:
                return [], []
            bounding_boxes = np.vstack(bounding_boxes)

            keep = nms(bounding_boxes[:, 0:5], nms_thresholds[0])
            bounding_boxes = bounding_boxes[keep]

            # use offsets predicted by pnet to transform bounding boxes
            bounding_boxes = calibrate_box(bounding_boxes[:, 0:5], bounding_boxes[:, 5:])
            # shape [n_boxes, 5]

            bounding_boxes = convert_to_square(bounding_boxes)
            bounding_boxes[:, 0:4] = np.round(bounding_boxes[:, 0:4])

            # STAGE 2

            img_boxes = get_image_boxes(bounding_boxes, image, size=24)
            img_boxes = torch.FloatTensor(img_boxes)

            output = self.rnet(img_boxes)
            offsets = output[0].cpu().data.numpy()  # shape [n_boxes, 4]
            probs = output[1].cpu().data.numpy()  # shape [n_boxes, 2]

            keep = np.where(probs[:, 1] > thresholds[1])[0]
            bounding_boxes = bounding_boxes[keep]
            bounding_boxes[:, 4] = probs[keep, 1].reshape((-1,))
            offsets = offsets[keep]

            keep = nms(bounding_boxes, nms_thresholds[1])
            bounding_boxes = bounding_boxes[keep]
            bounding_boxes = calibrate_box(bounding_boxes, offsets[keep])
            bounding_boxes = convert_to_square(bounding_boxes)
            bounding_boxes[:, 0:4] = np.round(bounding_boxes[:, 0:4])

            # STAGE 3

            img_boxes = get_image_boxes(bounding_boxes, image, size=48)
            if len(img_boxes) == 0:
                return [], []
            img_boxes = torch.FloatTensor(img_boxes)
            output = self.onet(img_boxes)
            landmarks = output[0].cpu().data.numpy()  # shape [n_boxes, 10]
            offsets = output[1].cpu().data.numpy()  # shape [n_boxes, 4]
            probs = output[2].cpu().data.numpy()  # shape [n_boxes, 2]

            keep = np.where(probs[:, 1] > thresholds[2])[0]
            bounding_boxes = bounding_boxes[keep]
            bounding_boxes[:, 4] = probs[keep, 1].reshape((-1,))
            offsets = offsets[keep]
            landmarks = landmarks[keep]

            # compute landmark points
            width = bounding_boxes[:, 2] - bounding_boxes[:, 0] + 1.0
            height = bounding_boxes[:, 3] - bounding_boxes[:, 1] + 1.0
            xmin, ymin = bounding_boxes[:, 0], bounding_boxes[:, 1]
            landmarks[:, 0:5] = (
                np.expand_dims(xmin, 1) + np.expand_dims(width, 1) * landmarks[:, 0:5]
            )
            landmarks[:, 5:10] = (
                np.expand_dims(ymin, 1) + np.expand_dims(height, 1) * landmarks[:, 5:10]
            )

            bounding_boxes = calibrate_box(bounding_boxes, offsets)
            keep = nms(bounding_boxes, nms_thresholds[2], mode="min")
            bounding_boxes = bounding_boxes[keep]
            landmarks = landmarks[keep]

        return bounding_boxes, landmarks
