"""Comparación estática: `/openapi.json` generado contra el contrato canónico.

Cubre lo que la validación de respuestas no puede ver. Medido en #33: una
implementación que olvida `le=100` en `pageSize` responde a `pageSize=1000` con
un `200` perfectamente conforme; solo esta comparación lo delata.

Qué compara, por operación implementada:

  - Los parámetros: presencia, `required` y esquema.
  - Las respuestas 2xx: estados, tipos de contenido y esquema. Es lo que ata el
    cierre de los modelos al del contrato, en los dos sentidos: desde docs#103
    el contrato declara `additionalProperties: false`, y un modelo sin
    `extra="forbid"` aparece como `código=None contrato=False`.

Qué NO compara, a propósito: los estados de error. `401/403/429/503` los emiten
la dependencia de identidad y la pasarela, y FastAPI añade un `422` que el
servicio nunca emite; en esta comparación serían ruido. Los errores son cosa de
`harness.assert_satisfies_contract`, sobre respuestas reales.

Los cuerpos de petición llegan en F3; hasta que esta comparación sepa hacerlo,
se NIEGA a comparar una operación que lleve uno, en lugar de darla por buena.
La única salida es nombrar el motivo (`without_body`), y entonces compara los
parámetros y las respuestas y deja fuera SOLO el cuerpo. La usan las tres
operaciones multipart de F5 (#37): FastAPI publica `UploadFile` con
`contentMediaType`, de OpenAPI 3.1, que esta traducción no sabe comparar, y el
cierre de esos cuerpos lo prueban peticiones reales con una parte de más
(`tests/contract/test_inference.py`).

EL NORMALIZADOR SE NIEGA ANTE LO QUE NO SABE COMPARAR. FastAPI genera OpenAPI
3.1 y el contrato es 3.0.3, así que antes de comparar hay que traducir. Toda
clave que no figure en `KNOWN` hace fallar la comparación con su nombre: una
traducción que ignorase en silencio `exclusiveMinimum` ---que es booleano en
3.0.3 y numérico en 3.1--- daría por iguales dos cotas distintas.

Y la traducción lee `nullable` según OAS 3.0.3 al pie de la letra ---solo
actúa si `type` está en el MISMO objeto---, que es también lo que hace
openapi-core. Desde docs#103 el contrato ya no tiene ningún `nullable` sin
`type` al lado (lo vigila `test_closure.py`), pero la lectura se queda: es la
correcta, y la otra daría por iguales dos esquemas distintos.

Dos composiciones del contrato que esta traducción NO sabe comparar y ante las
que se niega: el cierre de `WebhookEndpoint` (`allOf` más nombres y
`additionalProperties`) y el «X o nulo» de 3.0.3 (`anyOf: [X, NullValue]`).
Ninguna operación implementada las usa; la primera que lo haga tendrá que
enseñárselo.
"""

from tests.contract.harness import SPEC

KNOWN = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "format",
    "$ref",
    "allOf",
    "anyOf",
    "nullable",
    "title",
    "description",
    "default",
    "additionalProperties",
    "minimum",
    "maximum",
}
DOC_ONLY = {"title", "description"}
PARAMETER_KEYS = {"name", "in", "required", "schema", "description"}


class CannotCompare(ValueError):
    """El normalizador ha encontrado algo que no sabe traducir entre 3.1 y 3.0.3."""


def _deref(spec: dict, node: dict) -> dict:
    while "$ref" in node:
        target = spec
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def normalize(schema: dict, spec: dict, oas30: bool, where: str = "$") -> dict:
    unknown = set(schema) - KNOWN
    if unknown:
        raise CannotCompare(f"{where}: claves que no sé comparar: {sorted(unknown)}")
    s = {k: v for k, v in schema.items() if k not in DOC_ONLY}
    if "$ref" in s:
        if len(s) > 1:
            raise CannotCompare(f"{where}: $ref con hermanos {sorted(set(s) - {'$ref'})}")
        return normalize(_deref(spec, s), spec, oas30, where)
    nullable, wrapped = (_nullability_30 if oas30 else _nullability_31)(s, where)
    if wrapped is not None:
        inner = normalize(wrapped, spec, oas30, where)
        return {**inner, "nullable": True} if nullable else inner
    if "allOf" in s or "anyOf" in s:
        raise CannotCompare(f"{where}: composición que no sé comparar: {sorted(s)}")
    out = _structure(s, spec, oas30, where)
    if nullable:
        out["nullable"] = True
    return out


def _nullability_30(s: dict, where: str) -> tuple[bool, dict | None]:
    """3.0.3: `nullable` solo actúa con `type` en el MISMO objeto, y un `allOf` de
    un solo miembro es un envoltorio (la forma de poner `description` junto a un
    `$ref`)."""
    nullable = bool(s.pop("nullable", False) and "type" in s)
    if list(s) == ["allOf"] and len(s["allOf"]) == 1:
        return nullable, s["allOf"][0]
    return nullable, None


def _nullability_31(s: dict, where: str) -> tuple[bool, dict | None]:
    """3.1: FastAPI escribe «X o nulo» como `anyOf: [X, {type: null}]`."""
    if "anyOf" not in s:
        return False, None
    members = s.pop("anyOf")
    rest = [m for m in members if m != {"type": "null"}]
    if len(rest) != 1 or set(s) - {"default"}:
        raise CannotCompare(f"{where}: anyOf que no sé comparar: {members}")
    return len(rest) < len(members), rest[0]


def _structure(s: dict, spec: dict, oas30: bool, where: str) -> dict:
    out = {}
    if "properties" in s:
        out["properties"] = {
            k: normalize(v, spec, oas30, f"{where}.{k}") for k, v in s["properties"].items()
        }
    if "items" in s:
        out["items"] = normalize(s["items"], spec, oas30, f"{where}[]")
    extra = s.get("additionalProperties")
    if isinstance(extra, dict):
        out["additionalProperties"] = normalize(extra, spec, oas30, f"{where}{{*}}")
    elif "additionalProperties" in s:
        out["additionalProperties"] = extra
    for k in ("type", "enum", "format", "minimum", "maximum"):
        if k in s:
            out[k] = s[k]
    if "required" in s:
        out["required"] = sorted(s["required"])
    if s.get("default") is not None:
        out["default"] = s["default"]
    return out


def residual(code: dict, contract: dict, where: str = "$") -> list[str]:
    out = []
    for k in sorted(set(code) | set(contract)):
        if k in ("properties", "items", "additionalProperties") and (
            isinstance(code.get(k), dict) and isinstance(contract.get(k), dict)
        ):
            if k == "properties":
                pa, pb = code[k], contract[k]
                for p in sorted(set(pa) | set(pb)):
                    if p not in pa:
                        out.append(f"{where}.{p}: solo en el contrato")
                    elif p not in pb:
                        out.append(f"{where}.{p}: solo en el código")
                    else:
                        out += residual(pa[p], pb[p], f"{where}.{p}")
            else:
                out += residual(code[k], contract[k], f"{where}[{k}]")
        elif code.get(k) != contract.get(k):
            out.append(f"{where} [{k}]: código={code.get(k)!r} contrato={contract.get(k)!r}")
    return out


def _parameters(spec: dict, path: str, method: str) -> dict:
    item = spec["paths"][path]
    params = {}
    for raw in item.get("parameters", []) + item[method].get("parameters", []):
        param = _deref(spec, raw)
        unknown = set(param) - PARAMETER_KEYS
        if unknown:
            raise CannotCompare(f"parámetro {param['name']}: claves {sorted(unknown)}")
        params[(param["in"], param["name"])] = param
    return params


def _success(spec: dict, path: str, method: str) -> dict:
    responses = spec["paths"][path][method]["responses"]
    return {st: _deref(spec, r) for st, r in responses.items() if st.startswith("2")}


def compare_operation(
    generated: dict, method: str, path: str, *, without_body: str | None = None
) -> list[str]:
    """Diferencias entre la operación generada y la canónica; `[]` si coinciden.

    `without_body` es el MOTIVO por el que se compara sin el cuerpo de petición;
    sin él, una operación con cuerpo no se compara.
    """
    if without_body is None:
        for spec in (generated, SPEC):
            if "requestBody" in spec["paths"][path][method]:
                raise CannotCompare(f"{method} {path}: cuerpos de petición, no hasta F3")
    return _compare_parameters(generated, method, path) + _compare_success(generated, method, path)


def _compare_parameters(generated: dict, method: str, path: str) -> list[str]:
    diffs = []
    code, contract = _parameters(generated, path, method), _parameters(SPEC, path, method)
    for key in sorted(set(code) | set(contract)):
        where = f"parámetro {key[0]}:{key[1]}"
        if key not in contract:
            diffs.append(f"{where}: solo en el código")
        elif key not in code:
            diffs.append(f"{where}: solo en el contrato")
        else:
            a, b = code[key], contract[key]
            if a.get("required", False) != b.get("required", False):
                diffs.append(
                    f"{where} [required]: código={a.get('required')} contrato={b.get('required')}"
                )
            diffs += residual(
                normalize(a["schema"], generated, False, where),
                normalize(b["schema"], SPEC, True, where),
                where,
            )
    return diffs


def _compare_success(generated: dict, method: str, path: str) -> list[str]:
    diffs = []
    code, contract = _success(generated, path, method), _success(SPEC, path, method)
    if set(code) != set(contract):
        diffs.append(f"respuestas 2xx: código={sorted(code)} contrato={sorted(contract)}")
    for status in sorted(set(code) & set(contract)):
        a, b = code[status].get("content", {}), contract[status].get("content", {})
        if set(a) != set(b):
            diffs.append(f"{status}: tipos de contenido código={sorted(a)} contrato={sorted(b)}")
        for media in sorted(set(a) & set(b)):
            where = f"{status} {media} $"
            diffs += residual(
                normalize(a[media]["schema"], generated, False, where),
                normalize(b[media]["schema"], SPEC, True, where),
                where,
            )
    return diffs
