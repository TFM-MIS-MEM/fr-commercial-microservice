"""Comparación estática, con sus controles.

La comparación sobre el servicio real da `[]` hoy, y un `[]` no demuestra nada
por sí solo: también lo daría una comparación que no mirase. Por eso cada
control negativo va EMPAREJADO con su gemelo correcto sobre la misma operación:
el gemelo da `[]`, la versión defectuosa da exactamente la diferencia esperada,
y la única diferencia entre ambos es el defecto.

Desde docs#103 el contrato cierra sus esquemas, y la pareja del cierre se
invirtió: el gemelo correcto es el modelo con `extra="forbid"`, y el defectuoso,
cualquiera que no lo lleve. Un modelo que se quede abierto ya no pasa por
coincidir con un contrato abierto: la comparación lo da en rojo.
"""

import pytest
from fastapi import FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from src.main import create_app
from src.models.gallery import Gallery
from tests.contract.harness import IMPLEMENTED
from tests.contract.shape import CannotCompare, compare_operation
from tests.support.settings import settings

LIST = "/v1/fr/commercial/galleries"
ONE = "/v1/fr/commercial/galleries/{galleryId}"
WEBHOOK = "/v1/fr/commercial/webhooks/{webhookId}"


class GalleryPage(BaseModel):
    # Cerrado como el esquema inline del contrato, y ningún campo obligatorio:
    # ese esquema no declara `required`.
    model_config = ConfigDict(extra="forbid")

    items: list[Gallery] | SkipJsonSchema[None] = None
    page: int | SkipJsonSchema[None] = None
    pageSize: int | SkipJsonSchema[None] = None
    total: int | SkipJsonSchema[None] = None


def list_galleries(bounded: bool) -> dict:
    app = FastAPI()
    page_size = Query(20, ge=1, le=100) if bounded else Query(20, ge=1)

    @app.get(LIST, response_model=GalleryPage)
    def listing(page: int = Query(1, ge=1), pageSize: int = page_size):  # noqa: N803
        return {}

    return app.openapi()


def get_gallery_with(model) -> dict:
    app = FastAPI()

    @app.get(ONE, response_model=model)
    def one(galleryId: str):  # noqa: N803
        return {}

    return app.openapi()


# ------------------------------------------------------------ el servicio real


#: Operaciones que se comparan SIN su cuerpo de petición, con el motivo. Ver la
#: cabecera de `shape.py`.
MULTIPART = (
    "multipart: el cierre del cuerpo lo prueban peticiones con una parte de más "
    "(test_inference.py); la comparación no traduce `contentMediaType`"
)
WITHOUT_BODY = {
    ("post", "/v1/fr/commercial/detection"): MULTIPART,
    ("post", "/v1/fr/commercial/verification"): MULTIPART,
    ("post", "/v1/fr/commercial/extract-embeddings"): MULTIPART,
}


@pytest.mark.parametrize("method,path", sorted(IMPLEMENTED))
def test_every_implemented_operation_matches_the_contract(method, path):
    generated = create_app(settings()).openapi()

    reason = WITHOUT_BODY.get((method, path))
    assert compare_operation(generated, method, path, without_body=reason) == []


def test_an_operation_with_body_is_not_compared_without_a_reason():
    generated = create_app(settings()).openapi()

    with pytest.raises(CannotCompare, match="cuerpos de petición"):
        compare_operation(generated, "post", "/v1/fr/commercial/detection")


# ---------------------------------------------------- controles, en parejas


def test_control_pageSize_bounded_as_the_contract_says():
    assert compare_operation(list_galleries(bounded=True), "get", LIST) == []


def test_control_pageSize_without_le_100_is_red():
    # La cota de 100 es una regla de seguridad (CLAUDE.md, paginación), y la
    # respuesta a `pageSize=1000` de esta implementación sería CONFORME: solo
    # esta comparación la ve.
    assert compare_operation(list_galleries(bounded=False), "get", LIST) == [
        "parámetro query:pageSize [maximum]: código=None contrato=100"
    ]


def test_control_a_response_model_closed_as_the_contract():
    assert compare_operation(get_gallery_with(Gallery), "get", ONE) == []


def test_control_a_response_model_left_open_is_red():
    # El olvido del `forbid`: el valor por omisión de Pydantic (`extra="ignore"`)
    # no publica `additionalProperties`, que es un esquema abierto frente a uno
    # cerrado. Hasta docs#103 esta pareja daba `[]`, porque el contrato también
    # estaba abierto; hoy la comparación ya no puede pasarlo por alto.
    class Open(Gallery):
        model_config = ConfigDict(extra="ignore")

    assert compare_operation(get_gallery_with(Open), "get", ONE) == [
        "200 application/json $ [additionalProperties]: código=None contrato=False"
    ]


def test_control_a_response_model_that_lets_extra_fields_through_is_red():
    class Allow(Gallery):
        model_config = ConfigDict(extra="allow")

    assert compare_operation(get_gallery_with(Allow), "get", ONE) == [
        "200 application/json $ [additionalProperties]: código=True contrato=False"
    ]


# ------------------------------------------------------ el normalizador se niega


def test_the_normalizer_refuses_a_key_it_cannot_compare():
    # `gt` genera `exclusiveMinimum`: numérico en 3.1, booleano en 3.0.3.
    class Bounded(Gallery):
        identityCount: int = Field(gt=-1)

    with pytest.raises(
        CannotCompare, match=r"identityCount: claves que no sé comparar: \['exclusiveMinimum'\]"
    ):
        compare_operation(get_gallery_with(Bounded), "get", ONE)


def test_the_normalizer_refuses_the_closed_composite():
    # WebhookEndpoint es `allOf: [Base]` más nombres y `additionalProperties`
    # (docs#103). Comparar eso exige unir las propiedades de los miembros, que
    # este normalizador no sabe hacer: se NIEGA, en lugar de darlo por bueno.
    class Endpoint(BaseModel):
        model_config = ConfigDict(extra="forbid")

        id: str

    app = FastAPI()

    @app.get(WEBHOOK, response_model=Endpoint)
    def one(webhookId: str):  # noqa: N803
        return {}

    with pytest.raises(CannotCompare, match="composición que no sé comparar"):
        compare_operation(app.openapi(), "get", WEBHOOK)


def test_the_comparison_refuses_request_bodies_until_F3():
    app = FastAPI()

    @app.post(LIST)
    def create(body: Gallery):
        return {}

    with pytest.raises(CannotCompare, match="cuerpos de petición"):
        compare_operation(app.openapi(), "post", LIST)
