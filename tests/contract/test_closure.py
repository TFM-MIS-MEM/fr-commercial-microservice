"""Guardias estáticas del contrato: cierre de esquemas, `nullable` y `400`.

Las tres propiedades que introdujo docs#103 en `openapi/fr-commercial.yaml`, y
ninguna se puede dejar a la validación de respuestas:

  1. CIERRE (vía C del punto 3 de #33). Todo esquema de objeto declara
     `additionalProperties: false`, salvo los de `OPEN`, cada uno con su porqué.
     Un esquema nuevo que nazca abierto pone esto en rojo con su puntero.

  2. LAS DOS LISTAS DE NOMBRES DEL COMPUESTO. En 3.0.3, `additionalProperties`
     solo ve los `properties` de su MISMO objeto, no los de los miembros de un
     `allOf`. Por eso `WebhookEndpoint` y el `201` de `createWebhook` repiten los
     nombres de `WebhookEndpointBase`. El olvido tiene dos direcciones, medidas
     en #103, y solo una hace ruido por sí sola:

       - Un nombre de la base que FALTA en la lista: el validador rechaza una
         respuesta legítima. Ruidoso, pero solo si alguna prueba emite ese campo.
       - Un nombre de la lista que NO ESTÁ en la base: se admite con cualquier
         valor y sin validar. Silencioso: con `secret` de más en la lista de
         `WebhookEndpoint`, un `getWebhook` que devolviera el secreto pasaba.

     Esta prueba es lo único que ve la segunda.

  3. `400` DECLARADO donde rechazar es una decisión: toda operación con cuerpo
     (el campo de más se rechaza) y toda operación con un parámetro acotado
     (`minimum`, `maximum`, `enum`...).

ALCANCE DEL CIERRE EN LOS CUERPOS, sin redondear: que un campo de más dé `400`
está PROBADO en cuerpos JSON (`test_rejections.py`). En los 8 cuerpos
`multipart/form-data` es una obligación del contrato que vigila esta prueba y
que tienen que cumplir las operaciones que los implementen (F4, F5, F8, F12):
el mecanismo medido está en `src/models/__init__.py`.
"""

from tests.contract.harness import SPEC

COMPONENTS = "#/components/schemas"
WH_201 = ("/v1/fr/commercial/webhooks", "post", "201")

#: Esquemas de objeto que se quedan ABIERTOS, y por qué. Nada más.
OPEN = {
    f"{COMPONENTS}/WebhookEndpointBase": (
        "base que componen WebhookEndpoint y el 201 de createWebhook; cerrada, "
        "rechazaría el `secret` del 201"
    ),
    f"{COMPONENTS}/ErrorReport": "idéntico en los tres contratos; su cierre es docs#88",
    f"{COMPONENTS}/ErrorReport/properties/details": "abierto a propósito: datos de dominio",
}

METHODS = ("get", "post", "put", "patch", "delete")
BOUNDS = ("minimum", "maximum", "enum", "minLength", "maxLength", "pattern")


def _escape(key) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def _schemas(node, pointer="#"):
    """Todo objeto del documento, con su puntero JSON."""
    if isinstance(node, dict):
        yield pointer, node
        for key, value in node.items():
            yield from _schemas(value, f"{pointer}/{_escape(key)}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _schemas(value, f"{pointer}/{i}")


def _object_schemas():
    return {p: n for p, n in _schemas(SPEC) if n.get("type") == "object" or "properties" in n}


def _deref(node: dict) -> dict:
    while "$ref" in node:
        target = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _operations():
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                yield path, item, method, op


# ------------------------------------------------------------------ 1. cierre


def test_every_object_schema_is_closed_except_the_declared_open_ones():
    unclosed = [
        pointer
        for pointer, schema in _object_schemas().items()
        if schema.get("additionalProperties") is not False and pointer not in OPEN
    ]

    assert unclosed == []


def test_the_declared_open_ones_exist_and_are_open():
    # Sin esto, `OPEN` podría acumular punteros que ya no existen, y una exención
    # obsoleta es una puerta que nadie recuerda haber dejado abierta.
    objects = _object_schemas()

    assert sorted(p for p in OPEN if p not in objects) == []
    assert sorted(p for p in OPEN if objects[p].get("additionalProperties") is False) == []


def test_the_multipart_bodies_are_closed_too():
    # Nombrado aparte porque es donde el cierre del contrato va por delante del
    # código: hoy ninguna operación multipart está implementada.
    bodies = {
        op["operationId"]: op["requestBody"]["content"]["multipart/form-data"]["schema"]
        for _, _, _, op in _operations()
        if "multipart/form-data" in op.get("requestBody", {}).get("content", {})
    }

    assert len(bodies) == 8
    assert [
        op for op, schema in bodies.items() if schema.get("additionalProperties") is not False
    ] == []


# ----------------------------------------------------- 2. las listas del compuesto


def _wrapper_diffs() -> list[str]:
    schemas = SPEC["components"]["schemas"]
    if "WebhookEndpointBase" not in schemas:
        return ["falta WebhookEndpointBase: el compuesto no está reestructurado"]
    base = set(schemas["WebhookEndpointBase"]["properties"])
    path, method, status = WH_201
    created = SPEC["paths"][path][method]["responses"][status]["content"]["application/json"]
    out = []
    for label, wrapper, expected in (
        ("WebhookEndpoint", schemas["WebhookEndpoint"], base),
        ("201 de createWebhook", created["schema"], base | {"secret"}),
    ):
        names = set(wrapper.get("properties", {}))
        out += [
            f"{label}: '{n}' está en la base y falta en la lista -> se RECHAZARÍA una "
            "respuesta legítima"
            for n in sorted(expected - names)
        ]
        out += [
            f"{label}: '{n}' está en la lista y no en la base -> se ADMITIRÍA con cualquier "
            "valor, sin validar"
            for n in sorted(names - expected)
        ]
    return out


def test_the_closed_wrappers_list_exactly_the_names_of_their_base():
    assert _wrapper_diffs() == []


# ------------------------------------------------------------------ nullable


def test_no_nullable_without_type_in_the_same_object():
    # OAS 3.0.3: `nullable` añade `null` «only if `type` is explicitly defined
    # within the same Schema Object». Sin `type` al lado no hace nada, y dos
    # validadores conformes discreparon sobre ello (docs#103).
    assert [p for p, n in _schemas(SPEC) if n.get("nullable") and "type" not in n] == []


# ----------------------------------------------------------------- 3. el 400


def test_every_operation_with_a_body_declares_400():
    assert [
        op["operationId"]
        for _, _, _, op in _operations()
        if "requestBody" in op and "400" not in op["responses"]
    ] == []


def test_every_operation_with_a_bounded_parameter_declares_400():
    missing = []
    for _, item, _, op in _operations():
        params = [_deref(p) for p in item.get("parameters", []) + op.get("parameters", [])]
        bounded = [p["name"] for p in params if set(p["schema"]) & set(BOUNDS)]
        if bounded and "400" not in op["responses"]:
            missing.append(f"{op['operationId']}: {bounded}")

    assert missing == []
