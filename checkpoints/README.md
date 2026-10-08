# checkpoints/

Pesos de los modelos que carga el servicio al arrancar
(`src/inference/engine.py`). Se versionan directamente, sin LFS, porque cada
uno pesa menos de 100 MB, y se copian en la imagen (`Dockerfile`). Si falta
uno, el servicio no arranca. El código que los usa y su procedencia están en
`src/inference/README.md`.

| Fichero | Modelo | Bytes | SHA-256 | Licencia |
|---|---|---|---|---|
| `edgeface_xs_gamma_06.pt` | EdgeFace `xs` | 7.170.425 | `5ae7504cd9aee0a5d52c2115fd2eb66b0985dd1730f40134b5854e0cb658ce16` | CC BY-NC-SA 4.0 (`LICENSE-EdgeFace-weights`) |
| `edgeface_s_gamma_05.pt` | EdgeFace `s` | 14.695.737 | `dc59abda2e8580399fd115a1eeb07e1f21156196db604b884407bcf0f17efb07` | CC BY-NC-SA 4.0 (`LICENSE-EdgeFace-weights`) |
| `edgeface_base.pt` | EdgeFace `base` | 72.972.261 | `95861c09b22810136f43ec98845e7f09bfc3c43f5a804984a7bd2eac20abc30c` | CC BY-NC-SA 4.0 (`LICENSE-EdgeFace-weights`) |
| `mtcnn_pnet.npz` | MTCNN, P-Net | 30.042 | `689bd700426a394ae55938b79f8c7ea3d0a236f6b15c742b69f3ba61187327f5` | MIT (`LICENSE-MTCNN-weights`) |
| `mtcnn_rnet.npz` | MTCNN, R-Net | 405.046 | `4fe44c6981e08ae57ad2c4681138b4c3e4753cd7b346c37a1cf9ea0fa7dd788c` | MIT (`LICENSE-MTCNN-weights`) |
| `mtcnn_onet.npz` | MTCNN, O-Net | 1.561.830 | `4cc083b873e7752456a6caec711ee01c6b24c0dc2d55e2a2cf707afd3b2d147e` | MIT (`LICENSE-MTCNN-weights`) |

## EdgeFace

Copiados de fr-compilation (`weights/EdgeFace/`). Son **byte a byte** los de
`checkpoints/` en [otroshi/edgeface](https://github.com/otroshi/edgeface),
comprobado con el resumen git del blob, que es el que publica la API de
GitHub:

| Fichero | `git hash-object` local | Blob en otroshi/edgeface@`ce86851` |
|---|---|---|
| `edgeface_xs_gamma_06.pt` | `af5f8c5cf75e8b90d82d08f7a07ee10677327bfd` | idéntico |
| `edgeface_s_gamma_05.pt` | `07346953c3045297a5a9bddfb6a173fd27c4398e` | idéntico |
| `edgeface_base.pt` | `913d1d769d57a5251953afbe45b6dc59bf06f21a` | idéntico |

**Licencia: CC BY-NC-SA 4.0**, la de las fichas de Hugging Face de estos
modelos. El aviso completo está en `LICENSE-EdgeFace-weights`.
**Un despliegue comercial necesitaría otros pesos.**

Se cargan con `torch.load(..., weights_only=True)` y `strict=True`: no se
ejecuta pickle arbitrario, y un fichero que no case con su arquitectura impide
arrancar.

## MTCNN

Convertidos de los `.npy` de fr-compilation
(`face_alignment/mtcnn_pytorch/src/weights/{pnet,rnet,onet}.npy`, que son los de
[TropComplique/mtcnn-pytorch](https://github.com/TropComplique/mtcnn-pytorch),
extraídos de los modelos Caffe originales de
[kpzhang93/MTCNN_face_detection_alignment](https://github.com/kpzhang93/MTCNN_face_detection_alignment),
MIT). Los `.npy` eran un diccionario serializado con pickle, y había que
leerlos con `allow_pickle=True`. Los `.npz` contienen los mismos tensores con
los mismos nombres y se leen con `allow_pickle=False`.

La conversión, hecha una vez:

```python
src = np.load("pnet.npy", allow_pickle=True)[()]
np.savez("mtcnn_pnet.npz", **src)
```

Comprobado tensor a tensor (`np.array_equal` y el mismo `dtype`): 13, 16 y 21
tensores idénticos. SHA-256 de los originales: `pnet.npy`
`03e19e5c…66f18cb`, `rnet.npy` `5660aad6…2747083`, `onet.npy`
`313141c3…8b818f83`.
