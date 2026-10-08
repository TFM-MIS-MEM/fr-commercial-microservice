"""Modelos de la API: de entrada y de respuesta, TODOS cerrados.

El contrato cierra todo esquema propio (`additionalProperties: false`, docs#103):
lo que no declara, no lo admite. En el código eso es `extra="forbid"` en cada
modelo, de entrada y de respuesta. En uno de respuesta, porque si no la
comparación estática (`tests/contract/shape.py`) da
`additionalProperties: código=None contrato=False`. En uno de entrada, porque
un campo de más tiene que dar el `400` que declaran todas las operaciones con
cuerpo.

LO QUE ESTÁ PROBADO Y LO QUE ES OBLIGACIÓN. El `400` por campo de más está
probado en cuerpos JSON (`tests/contract/test_rejections.py`) y, desde F5 (#37),
en los tres cuerpos `multipart/form-data` de la inferencia sin estado
(`tests/contract/test_inference.py`). En los otros cinco es una obligación del
contrato, que vigila `tests/contract/test_closure.py` y que cumple quien
implemente F4, F8 y F12.
Medido en #103 con FastAPI 0.141.1 y Starlette 1.7.0:

  - Parámetros `Form()` y `File()` sueltos: una parte de más, de texto o de
    fichero, SE IGNORA y la respuesta es `200`. No sirven.
  - Un modelo con `extra="forbid"` anotado como `Annotated[Modelo, File()]`:
    las dos partes de más dan `400` (`extra_forbidden`), y FastAPI publica el
    cuerpo como `multipart/form-data`. ESTE es el mecanismo.
  - El mismo modelo anotado con `Form()` también rechaza, pero publica el
    cuerpo como `application/x-www-form-urlencoded`, que es falso frente al
    contrato y la comparación estática lo daría como diferencia.
  - Si la operación lee el cuerpo A MANO ---la vía que se midió en #33 para
    que F5 no lea el fichero antes de la identidad---, ningún modelo interviene:
    la obligación pasa a ser una lista explícita de las partes declaradas y un
    `400` ante cualquier otra.

`python-multipart`, que FastAPI exige para `Form` y `File`, entró en el árbol
fijado con F5 (#37). F5 no lee el flujo a mano: usa el modelo anotado con
`File()` y asume el coste de leer el cuerpo antes de la identidad; el tope de
bytes y la lectura tras la identidad son TFM-MIS-MEM/docs#116.
"""
