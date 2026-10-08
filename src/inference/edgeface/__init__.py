"""Los tres backbones de EdgeFace que sirve el servicio.

Las tres ramas de `get_model` de fr-compilation (`backbones/__init__.py`) que
corresponden a `xs`, `s` y `base`, con su fichero de pesos. Las demás ramas
(`r50`, `vit_*`, `swin_*`, `edgeface_xxs`, las cuantizadas) no se copian: el
contrato no las ofrece.
"""

from src.inference.edgeface.timmfr import get_timmfrv2, replace_linear_with_lowrank_2

BUILDERS = {
    # edgeface_xs_gamma_06
    "xs": lambda: replace_linear_with_lowrank_2(
        get_timmfrv2("edgenext_x_small", batchnorm=False), rank_ratio=0.6
    ),
    # edgeface_s_gamma_05
    "s": lambda: replace_linear_with_lowrank_2(
        get_timmfrv2("edgenext_small", batchnorm=False), rank_ratio=0.5
    ),
    # edgeface_base
    "base": lambda: get_timmfrv2("edgenext_base", batchnorm=False),
}

WEIGHTS = {
    "xs": "edgeface_xs_gamma_06.pt",
    "s": "edgeface_s_gamma_05.pt",
    "base": "edgeface_base.pt",
}
