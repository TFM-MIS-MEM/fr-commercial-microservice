# src/inference/ — procedencia y licencias

Código de inferencia **copiado** de `fr-compilation`
(`hardidwallet/fr-compilation`, `74c6921c`), el repositorio de evaluación de
modelos de reconocimiento facial del que salen los modelos y los umbrales de
este servicio. **No se usa `torch.hub`**: el servicio no descarga nada en
tiempo de ejecución. Los pesos están en `checkpoints/` (véase
`checkpoints/README.md`).

fr-compilation **no tiene fichero de licencia**. Cada pieza se copió de allí,
pero su licencia es la del proyecto del que fr-compilation la tomó, y se
acompaña de su texto original.

| Aquí | Origen en fr-compilation | Proyecto original | Licencia |
|---|---|---|---|
| `edgeface/timmfr.py` | `backbones/timmfr.py` | [otroshi/edgeface](https://github.com/otroshi/edgeface) @ `ce86851` | BSD-3-Clause, `edgeface/LICENSE` (copiado tal cual) |
| `edgeface/__init__.py` | las ramas `edgeface_xs_gamma_06`, `edgeface_s_gamma_05` y `edgeface_base` de `backbones/__init__.py` | ídem | ídem |
| `mtcnn/detector.py` | `face_alignment/mtcnn.py` | [mk-minchul/AdaFace](https://github.com/mk-minchul/AdaFace) @ `c60eaa7` | MIT, `mtcnn/LICENSE-AdaFace` |
| `mtcnn/align.py` | `face_alignment/mtcnn_pytorch/src/align_trans.py` y `matlab_cp2tform.py` | ídem | ídem |
| `mtcnn/nets.py` | `face_alignment/mtcnn_pytorch/src/get_nets.py` | [TropComplique/mtcnn-pytorch](https://github.com/TropComplique/mtcnn-pytorch) @ `45b3446` | MIT, `mtcnn/LICENSE-mtcnn-pytorch` |
| `mtcnn/box_utils.py` | `face_alignment/mtcnn_pytorch/src/box_utils.py` | ídem | ídem |
| `mtcnn/first_stage.py` | `face_alignment/mtcnn_pytorch/src/first_stage.py` | ídem | ídem |
| `lssc.py` | el método de `utils/tools_binarized.py` | fr-compilation | — (reimplementado, con los cortes calculados aquí) |

Lo propio del servicio: `engine.py` (carga, selección de rostro, preprocesado
sin torchvision) y `provenance.py` (token de procedencia).

## Los pesos de EdgeFace no son de uso comercial

El código de EdgeFace es BSD-3-Clause, pero **los pesos son CC BY-NC-SA 4.0**
(`checkpoints/LICENSE-EdgeFace-weights`) y se entrenaron con WebFace260M, de
uso no comercial. Este servicio es un trabajo académico. **Un despliegue
comercial necesitaría otros pesos**, o una licencia de sus titulares.

## Qué se ha cambiado al copiar, y qué no

**Regla: nada que cambie el vector.** Los cortes LSSC y los umbrales
(`lssc.py`) se fijaron sobre los vectores que este mismo código produjo en
fr-compilation (`results/EdgeFace/`). Medido antes de copiar, con torch
2.14.1+cpu y timm 1.0.30 frente a los 2.0.1 y 1.0.20 del original: en 86
imágenes de 30 identidades de LFW, el vector que produce este código y el
guardado en `results/` tienen un coseno ≥ 0,99994 (`xs`), ≥ 0,99986 (`s`) y
≥ 0,99985 (`base`).

Cambios:

- **Solo lo que se usa.** De los backbones, los tres EdgeFace del contrato. De
  la alineación, el camino de `crop_size=(112, 112)` con semejanza reflexiva;
  fuera las ramas `cv2_affine` y `affine`, cuyo `align_type is '...'` era una
  comparación por identidad con un literal (`SyntaxWarning`). De MTCNN, sin
  `align.py` (creaba un detector global al importarse) ni `detector.py`,
  `visualization_utils.py`, los modelos Caffe o los cuadernos.
- **Pesos sin pickle y sin `os.chdir`.** El original cambiaba el directorio de
  trabajo de todo el proceso para que `get_nets.py` encontrara sus `.npy` por
  ruta relativa, y los leía con `allow_pickle=True`. Ahora cada red recibe sus
  pesos, leídos de `checkpoints/mtcnn_*.npz` con `allow_pickle=False`.
- **Siempre CPU.** Fuera el parámetro `device`, su `assert` y el `device`
  global de `first_stage.py`.
- **Selección de rostro.** El original se quedaba con el primero que devolvía
  el NMS (`limit=1`); el servicio ordena por confianza y toma el mayor. Con un
  solo rostro, como en LFW, es lo mismo.
- **Preprocesado sin torchvision**: `ToTensor` + `Normalize(0.5, 0.5)` en
  numpy. El `Resize((112, 112))` del original no hace nada sobre un recorte que
  ya mide 112 × 112.
- `cp2tform:twoUniquePointsReq` es `ValueError` y no `Exception`, y el formato
  es el de ruff.

**Lo que no se ha corregido, a propósito.** `findSimilarity` refleja los puntos
de destino en su sitio antes de medir la primera norma, así que compara las dos
soluciones contra los puntos ya reflejados. Es un defecto del original, pero
corregirlo cambiaría la alineación y dejaría sin respaldo los cortes y los
umbrales. Está anotado en `mtcnn/align.py`.
