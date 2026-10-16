"""LSSC: que el código del servicio es el de `utils/tools_binarized.py`.

`script_lssc` es la binarización de fr-compilation copiada literalmente
(`encode` con `binarization_type='LSSC'` y la rama `bins.ndim == 1` de
`binarize`), sin vectorizar. La del servicio está vectorizada y empaquetada en
bytes; esta prueba es lo que dice que sigue siendo la misma.
"""

import numpy as np
import pytest

from src.inference import lssc


def script_lssc(vector: np.ndarray, bins: np.ndarray) -> np.ndarray:
    n_bins = len(bins) + 1
    interval_indices = np.searchsorted(bins, vector, side="right")
    return np.concatenate(
        [
            np.array(list(np.binary_repr(2**idx - 1, width=n_bins - 1)), dtype=int)
            for idx in interval_indices
        ],
        axis=0,
    )


@pytest.mark.parametrize("model", ["xs", "s", "base"])
def test_the_code_is_the_scripts_bit_for_bit(model):
    rng = np.random.default_rng(37)
    # Valores a la escala de los cortes del modelo, y los propios cortes, que
    # son el caso de borde de `side='right'`.
    scale = float(np.abs(lssc.BINS[model]).max()) * 2
    vectors = [rng.normal(0, scale, 512).astype(np.float32) for _ in range(20)]
    vectors.append(np.resize(lssc.BINS[model], 512))

    for vector in vectors:
        expected = np.packbits(script_lssc(vector, lssc.BINS[model]).astype(np.uint8)).tobytes()
        assert lssc.binarize(vector, model) == expected


def test_the_code_is_1536_bits_in_192_bytes():
    assert (lssc.CODE_BITS, lssc.CODE_BYTES) == (1536, 192)
    assert len(lssc.binarize(np.zeros(512, dtype=np.float32), "xs")) == 192


def test_hamming_is_the_l1_distance_of_the_interval_indices():
    rng = np.random.default_rng(1)
    a, b = rng.normal(0, 0.02, 512), rng.normal(0, 0.02, 512)
    bins = lssc.BINS["xs"]
    l1 = np.abs(np.searchsorted(bins, a, side="right") - np.searchsorted(bins, b, side="right"))

    assert lssc.hamming(lssc.binarize(a, "xs"), lssc.binarize(b, "xs")) == int(l1.sum())


def test_hamming_extremes():
    low, high = np.full(512, -1.0), np.full(512, 1.0)

    assert lssc.hamming(lssc.binarize(low, "s"), lssc.binarize(low, "s")) == 0
    assert lssc.hamming(lssc.binarize(low, "s"), lssc.binarize(high, "s")) == 1536


def test_the_bins_are_three_increasing_cuts_per_model():
    for bins in lssc.BINS.values():
        assert bins.shape == (3,)
        assert np.all(np.diff(bins) > 0)


@pytest.mark.parametrize(
    "model,d_max,threshold", [("xs", 479, 0.6882), ("s", 466, 0.6966), ("base", 520, 0.6615)]
)
def test_default_thresholds(model, d_max, threshold):
    assert lssc.D_MAX[model] == d_max
    assert round(lssc.default_threshold(model), 4) == threshold


@pytest.mark.parametrize("model", ["xs", "s", "base"])
def test_d_max_matches_and_one_more_does_not(model):
    threshold = lssc.default_threshold(model)

    assert lssc.confidence(lssc.D_MAX[model]) >= threshold
    assert lssc.confidence(lssc.D_MAX[model] + 1) < threshold


def test_hamming_many_is_hamming_row_by_row():
    # La búsqueda 1:N compara de una vez contra todos los códigos de la galería;
    # tiene que dar lo mismo que el comparador de pares, fila a fila.
    rng = np.random.default_rng(40)
    probe = rng.integers(0, 256, lssc.CODE_BYTES, dtype=np.uint8).tobytes()
    codes = [rng.integers(0, 256, lssc.CODE_BYTES, dtype=np.uint8).tobytes() for _ in range(50)]
    codes += [probe, bytes(lssc.CODE_BYTES), b"\xff" * lssc.CODE_BYTES]

    assert lssc.hamming_many(probe, codes).tolist() == [lssc.hamming(probe, c) for c in codes]


def test_hamming_many_of_no_codes_is_empty():
    assert lssc.hamming_many(bytes(lssc.CODE_BYTES), []).tolist() == []
