"""Arnés de contrato: cada respuesta, contra `openapi/fr-commercial.yaml`.

Es el equivalente de `toSatisfyApiSpec()` de jest-openapi en `auth` y `pricing`,
y se eligió en #33 frente a Schemathesis con un servicio mentiroso delante
(`test_harness.py`, que se queda como prueba permanente del propio arnés).

Tres piezas, y lo que cada una NO hace:

  1. `assert_satisfies_contract` — openapi-core, con el contrato canónico SIN
     tocar. Caza el estado no declarado, el tipo de contenido ajeno, el campo
     obligatorio que falta, el tipo, el formato y el `enum`. Y desde docs#103
     caza el campo de más, porque el contrato cierra sus esquemas (vía C del
     punto 3 de #33): el secreto de un punto de notificación devuelto por una
     consulta deja de ser conforme. La excepción es `ErrorReport`, que sigue
     abierto hasta docs#88. NO caza el cuerpo en una respuesta que no declara
     `content`: de eso se encarga el adaptador, en esta misma función.
  2. `assert_bare_error` — la única comprobación explícita que queda, y es
     estructural: un error que no es de dominio lleva exactamente `code` y
     `message`. `ErrorReport.details` es abierto a propósito, para datos de
     dominio, y ningún esquema puede decir «aquí no va una traza» sin cerrarlo;
     por eso esto no se puede delegar en el contrato. No se buscan marcas como
     `stack` en el cuerpo: una búsqueda así solo encuentra lo que ya se espera.
  3. La comparación estática entre el `/openapi.json` generado y el canónico
     vive en `shape.py`: cubre el lado de la petición, al que esta validación de
     respuestas es ciega.
"""

import re
from functools import cache
from pathlib import Path

import yaml
from openapi_core import OpenAPI
from openapi_core.datatypes import RequestParameters
from openapi_core.exceptions import OpenAPIError
from werkzeug.datastructures import Headers, ImmutableMultiDict

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi" / "fr-commercial.yaml"
SPEC = yaml.safe_load(SPEC_PATH.read_text())

#: Un `servers` del contrato. openapi-core rechaza cualquier otro anfitrión
#: (`ServerNotFound ... http://testserver`), medido en #33.
BASE_URL = "http://localhost:8000"

#: Operaciones implementadas, EXACTAMENTE. La recorren el guardián de identidad
#: y la comparación estática: una operación nueva obliga a tocar esta línea, y
#: con ello entra sola en las dos pruebas.
IMPLEMENTED = {
    ("post", "/v1/fr/commercial/galleries"),
    ("get", "/v1/fr/commercial/galleries"),
    ("get", "/v1/fr/commercial/galleries/{galleryId}"),
    ("patch", "/v1/fr/commercial/galleries/{galleryId}"),
    ("delete", "/v1/fr/commercial/galleries/{galleryId}"),
    ("post", "/v1/fr/commercial/detection"),
    ("post", "/v1/fr/commercial/verification"),
    ("post", "/v1/fr/commercial/extract-embeddings"),
    ("post", "/v1/fr/commercial/galleries/{galleryId}/identities"),
    ("get", "/v1/fr/commercial/galleries/{galleryId}/identities"),
    ("get", "/v1/fr/commercial/galleries/{galleryId}/identities/{identityId}"),
    ("patch", "/v1/fr/commercial/galleries/{galleryId}/identities/{identityId}"),
    ("delete", "/v1/fr/commercial/galleries/{galleryId}/identities/{identityId}"),
    ("post", "/v1/fr/commercial/galleries/{galleryId}/identities/{identityId}/faces"),
    ("delete", "/v1/fr/commercial/galleries/{galleryId}/identities/{identityId}/faces/{faceId}"),
}


@cache
def _openapi() -> OpenAPI:
    return OpenAPI.from_dict(SPEC)


class _Request:
    """Petición de httpx con la forma que espera openapi-core."""

    def __init__(self, request):
        self._request = request
        self.parameters = RequestParameters(
            query=ImmutableMultiDict(list(request.url.params.multi_items())),
            header=Headers(list(request.headers.multi_items())),
            cookie=ImmutableMultiDict(),
        )

    host_url = BASE_URL

    @property
    def path(self) -> str:
        return self._request.url.path

    @property
    def method(self) -> str:
        return self._request.method.lower()

    @property
    def body(self) -> bytes | None:
        return self._request.content or None

    @property
    def content_type(self) -> str:
        return self._request.headers.get("content-type", "")


class _Response:
    """Respuesta de httpx con la forma que espera openapi-core."""

    def __init__(self, response):
        self._response = response

    @property
    def data(self) -> bytes:
        return self._response.content

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def content_type(self) -> str:
        return self._response.headers.get("content-type", "")

    @property
    def headers(self) -> Headers:
        return Headers(list(self._response.headers.multi_items()))


def _template_for(path: str) -> str:
    for template in SPEC["paths"]:
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path):
            return template
    raise AssertionError(f"{path}: ninguna ruta del contrato la describe")


def _declared_response(method: str, path: str, status: int) -> dict:
    responses = SPEC["paths"][_template_for(path)][method]["responses"]
    declared = responses.get(str(status), responses.get("default"))
    if "$ref" in declared:
        declared = SPEC["components"]["responses"][declared["$ref"].rsplit("/", 1)[1]]
    return declared


def _explain(exc: BaseException) -> str:
    parts = []
    while exc is not None:
        parts.append(f"{type(exc).__name__}: {exc}")
        parts += [f"  - {error.message}" for error in getattr(exc, "schema_errors", ())]
        exc = exc.__cause__
    return "\n".join(parts)


def assert_satisfies_contract(response) -> None:
    """La respuesta es una de las que el contrato declara para esa operación."""
    request = response.request
    where = f"{request.method} {request.url.path} -> {response.status_code}"
    try:
        _openapi().validate_response(_Request(request), _Response(response))
    except OpenAPIError as exc:
        raise AssertionError(f"{where} no cumple el contrato:\n{_explain(exc)}") from None

    # openapi-core no mira el cuerpo cuando la respuesta no declara `content`: un
    # 204 con cuerpo le parece conforme (medido en #33). jest-openapi sí lo
    # cazaba, así que el hueco se tapa aquí y no operación por operación.
    declared = _declared_response(request.method.lower(), request.url.path, response.status_code)
    if "content" not in declared:
        assert response.content == b"", f"{where} no declara cuerpo y lleva {response.content!r}"


def assert_bare_error(response) -> None:
    """Un error que no es de dominio lleva exactamente `code` y `message`.

    «Que no es de dominio»: identidad, recurso inexistente, validación,
    plataforma e interno. Solo un error de dominio ---cuota, plan, conflicto---
    puede llevar `details`, y F1 no emite ninguno.
    """
    body = response.json()
    assert set(body) == {"code", "message"}, f"error con claves de más: {sorted(body)}"
