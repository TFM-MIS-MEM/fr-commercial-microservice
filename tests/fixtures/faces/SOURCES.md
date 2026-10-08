# Imágenes de prueba: origen y licencia

Tres retratos oficiales de la NASA, **de dominio público**: obras del Gobierno
federal de EE. UU. creadas por personal de la NASA en el ejercicio de sus
funciones (17 U.S.C. § 105). Wikimedia Commons las marca como
`Public domain` / `Copyrighted: False`.

No se usan imágenes de LFW: sus fotografías proceden de prensa y no tienen una
licencia clara para redistribuirlas.

| Fichero | Persona | Original en Wikimedia Commons | Autoría | Fecha | SHA-1 del original |
|---|---|---|---|---|---|
| `koch_2014.jpg` | Christina Koch (entonces Hammock) | [File:Christina M. Hammock portrait.jpg](https://commons.wikimedia.org/wiki/File:Christina_M._Hammock_portrait.jpg) ([Flickr de la NASA](https://www.flickr.com/photos/nasa2explore/12774511004/)) | Robert Markowitz, NASA | 2014-01-10 | `8d70b051fa71fa4282a96af64cd3250e5979a073` |
| `koch_2023.jpg` | Christina Koch | [File:Christina Koch Artemis 2 Crew Portrait.jpg](https://commons.wikimedia.org/wiki/File:Christina_Koch_Artemis_2_Crew_Portrait.jpg) | Josh Valcarcel, NASA | 2023-03-29 | `ad15fb3d1065a2f002923f22e04b016b91cb5355` |
| `williams_2018.jpg` | Sunita Williams | [File:Sunita Williams in 2018 (cropped).jpg](https://commons.wikimedia.org/wiki/File:Sunita_Williams_in_2018_(cropped).jpg) ([Flickr de la NASA](https://www.flickr.com/photos/nasa2explore/43757130882/)) | Robert Markowitz, NASA | 2018-07-31 | `121438b6bae018dd481c9b419c2fc0ff41f76317` |

**Transformación.** Cada original se redujo a 320 píxeles de lado mayor con
Pillow (`thumbnail`, `LANCZOS`), en RGB y JPEG de calidad 90, sin metadatos.
Los tres originales tienen orientación EXIF 1, así que no hubo que girarlos.

**Para qué sirven.** Una pareja de la misma persona, nueve años aparte, y otra
de personas distintas. Medido con el código de este PR (#37), con el umbral por
omisión de cada modelo:

| Modelo | Koch 2014 / Koch 2023 | Koch 2014 / Williams | Koch 2023 / Williams | Umbral |
|---|---|---|---|---|
| `xs` | d = 442 · 0,7122 ✔ | d = 552 · 0,6406 ✘ | d = 620 · 0,5964 ✘ | 0,6882 |
| `s` | d = 387 · 0,7480 ✔ | d = 537 · 0,6504 ✘ | d = 582 · 0,6211 ✘ | 0,6966 |
| `base` | d = 406 · 0,7357 ✔ | d = 536 · 0,6510 ✘ | d = 544 · 0,6458 ✘ | 0,6615 |

El margen más estrecho es el de `base` con Koch 2014 / Williams (0,6510 frente
a 0,6615). La inferencia es determinista en CPU, pero si una actualización de
torch o de timm moviera los vectores, esa es la primera prueba que cambiaría.
