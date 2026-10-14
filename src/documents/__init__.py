"""Modelos de PERSISTENCIA: la forma de los documentos en MongoDB (docs#34).

No son esquemas del contrato, y por eso no viven en `src/models`, que es de los
modelos de la API y los cierra todos con `extra="forbid"`. Aquí rige otra cosa:

  - AL LEER, lo que no se modela se IGNORA (`extra="ignore"`). Un documento
    puede llevar campos que esta versión no conoce, y lo que no se nombra no
    sale: la respuesta pasa siempre por un modelo de `src/models`, cerrado.
  - AL ESCRIBIR, lo opcional sin valor se OMITE, campo a campo y no con
    `exclude_none` global, por el mismo motivo que en `src/models/gallery.py`.
    En `externalId` es además lo que deja a la identidad fuera del índice único
    parcial: se guarda ausente, nunca `null`.
  - `_id` lo asigna MongoDB al insertar; un documento nuevo no lo lleva.

Cada módulo declara su `COLLECTION`, sus `INDEXES` y, para cada índice único,
el código de dominio de su `409` en `CONFLICTS`. `ensure_indexes`
(`src/core/database.py`) los crea al arrancar; `tests/contract/
test_uniqueness.py` cruza los únicos con los `409` del contrato.

QUÉ NO HAY AQUÍ: mantener los contadores es de F3 y F4, la reconciliación es
otra fase, y el formato binario de `embedding` es de F6.
"""

from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError

from src.documents import face, gallery, identity

#: Colección -> índices que el servicio crea al arrancar.
INDEXES: dict[str, list[IndexModel]] = {
    module.COLLECTION: module.INDEXES for module in (gallery, identity, face)
}

#: Nombre del índice único -> código de dominio de su `409`.
#:
#: CÓMO LO USAN F3 Y F4. Se escribe sin comprobar antes: la consulta previa
#: tiene una carrera entre la lectura y la escritura, y el índice es la única
#: garantía. El `DuplicateKeyError` se traduce a `409` según EL ÍNDICE QUE
#: SALTÓ ---`details["keyPattern"]`, con `conflict_code`---, no solo por el
#: código 11000: una colección con dos índices únicos tendría dos `409`
#: distintos, y un 11000 de un índice no previsto no es un `409` declarado.
#: Medido en #34: el error de inserción lleva `keyPattern` y `keyValue`; en #35,
#: que el de `find_one_and_update` también, pero sin `index`.
CONFLICTS: dict[str, str] = {**gallery.CONFLICTS, **identity.CONFLICTS}


def conflict_code(exc: DuplicateKeyError) -> str | None:
    """Código de dominio del índice único que saltó; `None` si no es uno previsto.

    El nombre del índice se DERIVA de `keyPattern` ---el nombre por omisión de
    MongoDB es `campo_1_campo_1`, el mismo que usan las claves de `CONFLICTS`---
    y no se lee de `details["index"]`, porque esa clave no existe en todos los
    caminos de escritura. Medido en #35 contra mongod 7.0: el error de
    `insert_one` lleva `index` y `keyPattern`; el de `find_one_and_update`
    (`findAndModify`) lleva `keyPattern` y NO `index`.

    Quien recibe `None` relanza: un 11000 de un índice que no figura en
    `CONFLICTS` no es un `409` declarado, y sale como `500`.
    """
    pattern = (exc.details or {}).get("keyPattern") or {}
    name = "_".join(f"{field}_{direction}" for field, direction in pattern.items())
    return CONFLICTS.get(name)
