"""Binarización LSSC y comparación por distancia de Hamming.

EL MÉTODO, reproducido tal cual de fr-compilation, `utils/tools_binarized.py`
(el único código de ese repositorio que binariza; no hay un script de
binarización aparte del de evaluación):

  - Cuatro intervalos equiprobables, con TRES CORTES GLOBALES: los cuantiles
    0,25 / 0,5 / 0,75 de todos los valores de todas las dimensiones juntas
    (`calculate_quantile_bins`, líneas 83-105; los cuantiles, 102-103).
  - Cada dimensión se sustituye por su índice de intervalo
    (`np.searchsorted(bins, value, side='right')`, línea 70) y el índice se
    codifica como termómetro de `n_bins - 1 = 3` bits: 0 → 000, 1 → 001,
    2 → 011, 3 → 111 (`encode`, rama `LSSC`, líneas 41-44).
  - 512 dimensiones × 3 bits = 1536 bits = 192 bytes. La distancia de Hamming
    entre dos códigos es la suma de las diferencias absolutas de sus índices.
  - SOBRE EL VECTOR SIN NORMALIZAR: `load_float_embeddings` (líneas 256-282)
    carga los `.npy` tal cual y el `__main__` (líneas 519-522) se los pasa sin
    tocar. El servicio hace lo mismo, y por eso el vector real que devuelve
    `extractEmbedding` también sale sin normalizar.

LOS CORTES, constantes de la versión `v1` de cada modelo. Calculados con
`calculate_quantile_bins` SOBRE TODAS LAS IDENTIDADES: los `.npy` de
`results/EdgeFace/LFW_EdgeFace_{xs,s,base}_embeddings/` de fr-compilation, 4309
identidades y 9974 vectores por modelo (5.106.688 valores). El script muestrea
500 identidades al azar y sin semilla solo para ir más rápido; fijar una
semilla habría convertido ese atajo en una constante arbitraria. Con las
semillas 0-9 el umbral se mueve unas pocas unidades de Hamming y el TAR, como
mucho una centésima (tabla en el PR de #37).

Cambiar un corte cambia el código de todo rostro: los códigos ya guardados
dejarían de ser comparables con los nuevos. Por eso son parte de la versión y
no configuración.

LOS UMBRALES, `D_MAX`: la mayor distancia con una tasa de falsa aceptación
≤ 1e-4 en LFW, con esos mismos cortes. Pares: los 215.785 genuinos y 4.498.500
impostores (3000 identidades, una muestra aleatoria de cada una, semilla 0),
el protocolo de `utils/tools.py`. LFW es un conjunto fácil y ya alineado: son
umbrales de partida, no una garantía para fotografías reales.

  modelo  d_max  confidence  TAR @ FAR ≤ 1e-4
  xs      479    0,6882      99,60 %
  s       466    0,6966      99,71 %
  base    520    0,6615      99,79 %
"""

import numpy as np

DIMENSIONS = 512
BITS_PER_DIMENSION = 3
CODE_BITS = DIMENSIONS * BITS_PER_DIMENSION  # 1536
CODE_BYTES = CODE_BITS // 8  # 192

BINS = {
    "xs": np.array([-0.009089103201404214, 1.8398393876850605e-05, 0.009139359928667545]),
    "s": np.array([-0.08147257007658482, -0.0005671345861628652, 0.08010426349937916]),
    "base": np.array([-0.11697322316467762, -0.0008006414282135665, 0.11550697311758995]),
}

D_MAX = {"xs": 479, "s": 466, "base": 520}

# `encode(idx, 4, 'LSSC')`: `np.binary_repr(2**idx - 1, width=3)`.
_THERMOMETER = np.array([[0, 0, 0], [0, 0, 1], [0, 1, 1], [1, 1, 1]], dtype=np.uint8)


def binarize(vector: np.ndarray, model: str) -> bytes:
    """El código LSSC de un vector, empaquetado con el bit más significativo primero."""
    indices = np.searchsorted(BINS[model], vector, side="right")
    bits = _THERMOMETER[indices].reshape(-1)
    return np.packbits(bits).tobytes()


def hamming(a: bytes, b: bytes) -> int:
    xor = np.bitwise_xor(np.frombuffer(a, dtype=np.uint8), np.frombuffer(b, dtype=np.uint8))
    return int(np.bitwise_count(xor).sum())


def confidence(distance: int) -> float:
    return 1 - distance / CODE_BITS


def default_threshold(model: str) -> float:
    """El umbral por omisión en la escala de `confidence`: `1 − d_max/1536`.

    Se calcula con la MISMA expresión que `confidence`, de modo que una
    distancia igual a `d_max` da exactamente el umbral y cuenta como coincidencia.
    """
    return confidence(D_MAX[model])
