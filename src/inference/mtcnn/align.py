"""Alineación por semejanza a los cinco puntos de referencia, recorte de 112 × 112.

Copiado de fr-compilation, `face_alignment/mtcnn_pytorch/src/align_trans.py` y
`matlab_cp2tform.py` (AdaFace, MIT: `LICENSE-AdaFace`). SOLO el camino que
recorre `MTCNN.align_multi` con `crop_size=(112, 112)`:

  - `get_reference_facial_points(default_square=True)` sin `output_size` ni
    relleno, que devuelve los puntos de 96 × 112 desplazados a un cuadrado de
    112 × 112. Las ramas de relleno y de otros tamaños no se copian.
  - `warp_and_crop_face` con `align_type` por omisión (semejanza). Las ramas
    `cv2_affine` y `affine` no se copian; en el original se elegían con
    `align_type is '...'`, una comparación por identidad con un literal que
    Python 3.12 avisa como `SyntaxWarning`.
  - De `matlab_cp2tform`: la semejanza reflexiva, que es la que usa
    `get_similarity_transform_for_cv2` por omisión.

LO QUE NO SE HA CORREGIDO, A PROPÓSITO. `findSimilarity` refleja `xy` EN SU
SITIO (`xyR = xy` no copia) antes de medir `norm1`, de modo que las dos normas
se calculan contra los puntos ya reflejados. Es un defecto del original, pero es
el que produjo los vectores de `results/` con los que se fijaron los cortes LSSC
y los umbrales (`src/inference/lssc.py`): corregirlo cambiaría la alineación de
algunas caras y dejaría esas constantes sin respaldo. Medido: con este código,
el vector de 86 imágenes de LFW coincide con el de `results/` con coseno
≥ 0,99985 en los tres modelos.
"""

import cv2
import numpy as np
from numpy.linalg import inv, lstsq, norm
from numpy.linalg import matrix_rank as rank

# reference facial points, a list of coordinates (x,y)
REFERENCE_FACIAL_POINTS = [
    [30.29459953, 51.69630051],
    [65.53179932, 51.50139999],
    [48.02519989, 71.73660278],
    [33.54930115, 92.3655014],
    [62.72990036, 92.20410156],
]

DEFAULT_CROP_SIZE = (96, 112)


def get_square_reference_facial_points():
    """`get_reference_facial_points(default_square=True)`, rama sin relleno."""
    tmp_5pts = np.array(REFERENCE_FACIAL_POINTS)
    tmp_crop_size = np.array(DEFAULT_CROP_SIZE)

    # 0) make the inner region a square
    size_diff = max(tmp_crop_size) - tmp_crop_size
    tmp_5pts += size_diff / 2
    return tmp_5pts


def tformfwd(trans, uv):
    uv = np.hstack((uv, np.ones((uv.shape[0], 1))))
    xy = np.dot(uv, trans)
    xy = xy[:, 0:-1]
    return xy


def findNonreflectiveSimilarity(uv, xy):  # noqa: N802 — el nombre del original
    K = 2
    M = xy.shape[0]
    x = xy[:, 0].reshape((-1, 1))  # use reshape to keep a column vector
    y = xy[:, 1].reshape((-1, 1))  # use reshape to keep a column vector

    tmp1 = np.hstack((x, y, np.ones((M, 1)), np.zeros((M, 1))))
    tmp2 = np.hstack((y, -x, np.zeros((M, 1)), np.ones((M, 1))))
    X = np.vstack((tmp1, tmp2))

    u = uv[:, 0].reshape((-1, 1))  # use reshape to keep a column vector
    v = uv[:, 1].reshape((-1, 1))  # use reshape to keep a column vector
    U = np.vstack((u, v))

    # We know that X * r = U
    if rank(X) >= 2 * K:
        r, _, _, _ = lstsq(X, U)
        r = np.squeeze(r)
    else:
        raise ValueError("cp2tform:twoUniquePointsReq")

    sc = r[0]
    ss = r[1]
    tx = r[2]
    ty = r[3]

    Tinv = np.array([[sc, -ss, 0], [ss, sc, 0], [tx, ty, 1]])

    T = inv(Tinv)

    T[:, 2] = np.array([0, 0, 1])

    return T


def findSimilarity(uv, xy):  # noqa: N802 — el nombre del original
    # Solve for trans1
    trans1 = findNonreflectiveSimilarity(uv, xy)

    # Solve for trans2

    # manually reflect the xy data across the Y-axis
    # (EN SU SITIO, como el original: véase la cabecera del módulo)
    xyR = xy
    xyR[:, 0] = -1 * xyR[:, 0]

    trans2r = findNonreflectiveSimilarity(uv, xyR)

    # manually reflect the tform to undo the reflection done on xyR
    TreflectY = np.array([[-1, 0, 0], [0, 1, 0], [0, 0, 1]])

    trans2 = np.dot(trans2r, TreflectY)

    # Figure out if trans1 or trans2 is better
    xy1 = tformfwd(trans1, uv)
    norm1 = norm(xy1 - xy)

    xy2 = tformfwd(trans2, uv)
    norm2 = norm(xy2 - xy)

    if norm1 <= norm2:
        return trans1
    return trans2


def get_similarity_transform_for_cv2(src_pts, dst_pts):
    trans = findSimilarity(src_pts, dst_pts)
    # cvt_tform_mat_for_cv2
    return trans[:, 0:2].T


def warp_and_crop_face(src_img, facial_pts, reference_pts, crop_size):
    """Semejanza de los cinco puntos detectados a los de referencia, y recorte."""
    ref_pts = np.float32(reference_pts)
    src_pts = np.float32(facial_pts)
    tfm = get_similarity_transform_for_cv2(src_pts, ref_pts)
    face_img = cv2.warpAffine(src_img, tfm, (crop_size[0], crop_size[1]))
    return face_img
