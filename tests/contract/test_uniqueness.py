"""Guardia estática: ningún índice único sin su `409` declarado (docs#34).

Un índice único convierte un valor repetido en un `DuplicateKeyError`. Si la
operación que lo escribe no declara el `409`, ese error sale por una respuesta
que el contrato no declara. Esta prueba cruza las dos fuentes, y ninguna se
compara consigo misma:

  - Los índices únicos salen del CÓDIGO: `INDEXES` de `src/documents`, con su
    código de dominio en `CONFLICTS` y sus campos opcionales en el filtro
    parcial. Un índice único sin entrada en `CONFLICTS`, o una entrada sin
    índice, también es un hueco.
  - Las operaciones afectadas salen del CONTRATO: toda operación cuyo
    `requestBody` declare un campo de la clave del índice. Los campos de ámbito
    (`accountId`, que llega en cabecera; `galleryId`, que llega en la ruta) no
    están en ningún cuerpo y por eso no arrastran operaciones.

Lo que exige, por índice:

  1. Que alguna operación pueda violarlo. Un índice único que ningún cuerpo
     alcanza es una garantía sin origen: o sobra, o la tabla se ha quedado vieja.
  2. Que todas las operaciones afectadas declaren `409` con el código de dominio
     del índice en la descripción, y con el MISMO `$ref`: la descripción no se
     duplica por operación (CLAUDE.md, regla 6).
  3. Que el campo opcional que el índice excluye cuando falta no admita la
     cadena vacía en ningún cuerpo (`minLength` >= 1). El índice parcial indexa
     `""` (medido en #34), y un campo multipart en blanco llega como `""`: la
     segunda identidad «sin `externalId`» daría un `409` falso. No se normaliza
     `""` a ausente en el código, porque sería ajustar en silencio; se rechaza
     con el `400` que la operación ya declara.

LÍMITE CONOCIDO. Las operaciones se eligen por NOMBRE de campo. Si mañana un
cuerpo de otro recurso declarara `name`, esta prueba le exigiría el `409` de las
galerías. Se pondría en rojo, que es lo correcto: obliga a decidir en vez de
pasar en silencio.
"""

from dataclasses import dataclass

import pytest
from pymongo import ASCENDING, IndexModel

from src.documents import CONFLICTS, INDEXES
from tests.contract.test_closure import _deref, _operations

#: Campos que llegan por cabecera o por ruta, nunca por el cuerpo.
SCOPE = {"accountId", "galleryId"}


@dataclass(frozen=True)
class Unique:
    collection: str
    keys: tuple[str, ...]
    code: str
    #: Campos que el índice excluye cuando faltan (filtro parcial).
    optional: tuple[str, ...] = ()


def unique_indexes(indexes, conflicts) -> tuple[list[Unique], list[str]]:
    """Los únicos de `INDEXES`, y los huecos entre `INDEXES` y `CONFLICTS`."""
    uniques, gaps, seen = [], [], set()
    for collection, models in indexes.items():
        for model in models:
            spec = model.document
            if not spec.get("unique"):
                continue
            seen.add(spec["name"])
            if spec["name"] not in conflicts:
                gaps.append(f"{collection} {spec['name']}: único sin código en CONFLICTS")
                continue
            uniques.append(
                Unique(
                    collection,
                    tuple(spec["key"]),
                    conflicts[spec["name"]],
                    optional=tuple(spec.get("partialFilterExpression", {})),
                )
            )
    gaps += [
        f"CONFLICTS {name}: no hay índice único con ese nombre"
        for name in conflicts
        if name not in seen
    ]
    return uniques, gaps


def _writers(field: str, spec_ops, deref) -> list[tuple[str, dict]]:
    return [
        (op["operationId"], schema["properties"][field])
        for op in spec_ops
        for media in op.get("requestBody", {}).get("content", {}).values()
        if field in (schema := deref(media["schema"])).get("properties", {})
    ]


def uniqueness_gaps(uniques, spec_ops, deref=_deref) -> list[str]:
    """Cada incumplimiento, en una línea legible."""
    spec_ops = list(spec_ops)
    by_id = {op["operationId"]: op for op in spec_ops}
    out = []
    for index in uniques:
        label = f"{index.collection} {{{', '.join(index.keys)}}}"
        body_keys = [k for k in index.keys if k not in SCOPE]
        writers = sorted({op for k in body_keys for op, _ in _writers(k, spec_ops, deref)})
        if not writers:
            out.append(f"{label}: ninguna operación puede violarlo")
            continue

        refs = set()
        for op_id in writers:
            raw = by_id[op_id]["responses"].get("409")
            if raw is None:
                out.append(f"{label}: {op_id} no declara 409")
                continue
            refs.add(raw.get("$ref", f"(en línea en {op_id})"))
            if f"`{index.code}`" not in deref(raw).get("description", ""):
                out.append(f"{label}: el 409 de {op_id} no nombra `{index.code}`")
        if len(refs) > 1:
            out.append(f"{label}: el 409 no es un único $ref compartido: {sorted(refs)}")

        for field in index.optional:
            for op_id, schema in _writers(field, spec_ops, deref):
                if schema.get("minLength", 0) < 1:
                    out.append(
                        f"{label}: {op_id} admite {field} vacío (sin minLength >= 1); "
                        "el índice parcial lo indexaría como valor"
                    )
    return out


def _spec_ops():
    return [op for _, _, _, op in _operations()]


def _gaps(indexes, conflicts) -> list[str]:
    uniques, gaps = unique_indexes(indexes, conflicts)
    return gaps + uniqueness_gaps(uniques, _spec_ops())


def test_every_unique_index_has_its_409_declared():
    assert _gaps(INDEXES, CONFLICTS) == []


def test_the_service_declares_the_two_unique_indexes_of_claude_md():
    # Sin esto, vaciar INDEXES dejaría la guardia en verde sin vigilar nada.
    uniques, _ = unique_indexes(INDEXES, CONFLICTS)

    assert {(u.collection, u.keys, u.optional) for u in uniques} == {
        ("galleries", ("accountId", "name"), ()),
        ("identities", ("galleryId", "externalId"), ("externalId",)),
    }


# --------------------------------------------------------------- controles


INJECTED = IndexModel([("accountId", ASCENDING), ("url", ASCENDING)], unique=True)


def test_control_a_unique_index_injected_into_indexes_without_409_is_red():
    # Contra INDEXES y el contrato REALES, con un índice de más: `url` está en
    # los cuerpos de createWebhook y updateWebhook, y ninguno declara 409.
    indexes = {**INDEXES, "webhook_endpoints": [INJECTED]}
    conflicts = {**CONFLICTS, "accountId_1_url_1": "URL_ALREADY_EXISTS"}

    assert _gaps(indexes, conflicts) == [
        "webhook_endpoints {accountId, url}: createWebhook no declara 409",
        "webhook_endpoints {accountId, url}: updateWebhook no declara 409",
    ]


def test_control_a_unique_index_without_code_or_a_code_without_index_is_red():
    indexes = {**INDEXES, "webhook_endpoints": [INJECTED]}
    conflicts = {**CONFLICTS, "huerfano_1": "ORPHAN"}

    assert _gaps(indexes, conflicts) == [
        "webhook_endpoints accountId_1_url_1: único sin código en CONFLICTS",
        "CONFLICTS huerfano_1: no hay índice único con ese nombre",
    ]


def _op(op_id, props, conflict=None):
    responses = {"400": {"description": "x"}}
    if conflict is not None:
        responses["409"] = conflict
    return {
        "operationId": op_id,
        "requestBody": {"content": {"application/json": {"schema": {"properties": props}}}},
        "responses": responses,
    }


SHARED = {"$ref": "#/components/responses/Shared"}
CONTROLS = {
    "conforme": (
        [_op("a", {"k": {"minLength": 1}}, SHARED), _op("b", {"k": {"minLength": 1}}, SHARED)],
        [],
    ),
    "el 409 no nombra el código": (
        [_op("a", {"k": {"minLength": 1}}, {"description": "otro `OTRO`"})],
        ["c {galleryId, k}: el 409 de a no nombra `K_TAKEN`"],
    ),
    "dos 409 en línea en vez de un $ref": (
        [
            _op("a", {"k": {"minLength": 1}}, {"description": "`K_TAKEN`"}),
            _op("b", {"k": {"minLength": 1}}, {"description": "`K_TAKEN`"}),
        ],
        [
            "c {galleryId, k}: el 409 no es un único $ref compartido: "
            "['(en línea en a)', '(en línea en b)']"
        ],
    ),
    "opcional sin minLength": (
        [_op("a", {"k": {}}, SHARED)],
        [
            "c {galleryId, k}: a admite k vacío (sin minLength >= 1); "
            "el índice parcial lo indexaría como valor"
        ],
    ),
    "ningún cuerpo lo alcanza": (
        [_op("a", {"otro": {}}, SHARED)],
        ["c {galleryId, k}: ninguna operación puede violarlo"],
    ),
}


@pytest.mark.parametrize("ops,expected", CONTROLS.values(), ids=CONTROLS.keys())
def test_control_uniqueness_gaps(ops, expected):
    # Contrato sintético: el $ref compartido se resuelve aquí, no contra el real.
    def deref(node):
        return {"description": "`K_TAKEN`"} if node is SHARED else node

    index = Unique("c", ("galleryId", "k"), "K_TAKEN", optional=("k",))

    assert uniqueness_gaps([index], ops, deref) == expected
