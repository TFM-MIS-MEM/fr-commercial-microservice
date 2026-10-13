# Imágenes de la campaña de medición: origen y licencia

Las dos mismas fotografías que `tests/fixtures/faces/koch_2014.jpg` y
`koch_2023.jpg`, retratos oficiales de la NASA **de dominio público** (17 U.S.C.
§ 105). Origen, autoría y SHA-1 de los originales, en
[`tests/fixtures/faces/SOURCES.md`](../../tests/fixtures/faces/SOURCES.md). Se
eligen porque el par ya está caracterizado: misma persona, nueve años aparte, y
los tres modelos lo dan como `isTheSame` con el umbral por omisión.

**Una sola resolución para toda la campaña: 480 × 640** (0,307 MP, el tramo de
menos de 0,5 MP). Vertical y no 640 × 480 porque los dos originales son retratos
3:4 (3000 × 4000 y 1095 × 1460): se reducen sin recortar ni deformar. El coste de
MTCNN crece con los píxeles (su pirámide de escalas), así que la resolución es
parte de la definición del perfil, no un detalle.

**Transformación.** Original descargado de Wikimedia Commons el 2026-10-09, con
el SHA-1 que consta en el `SOURCES.md` de las pruebas (comprobado). Con Pillow:
`convert("RGB")`, `resize((480, 640), LANCZOS)` y JPEG de calidad 90, sin
metadatos. Los dos originales tienen orientación EXIF 1.

| Fichero | Original | Bytes | SHA-256 |
|---|---|---|---|
| `koch_2014_480x640.jpg` | `Christina_M._Hammock_portrait.jpg` | 69 371 | `aab16e107dcff073f2c670251eb0b5936880dfa5fd85fa9a1f92b8c8e2e33467` |
| `koch_2023_480x640.jpg` | `Christina_Koch_Artemis_2_Crew_Portrait.jpg` | 85 922 | `cdaf4235600400f8c1da8c50f4478d03b2e7c1632c1fe83e5e74558025a25851` |
