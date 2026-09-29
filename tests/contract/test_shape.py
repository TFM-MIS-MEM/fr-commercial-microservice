"""Comparación estática, con sus controles.

La comparación sobre el servicio real da `[]` hoy, y un `[]` no demuestra nada
por sí solo: también lo daría una comparación que no mirase. Por eso cada
control negativo va EMPAREJADO con su gemelo correcto sobre la misma operación:
el gemelo da `[]`, la versión defectuosa da exactamente la diferencia esperada,
y la única diferencia entre ambos es el defecto.
"""

import pytest
from fastapi import FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from src.core.settings import Settings
from src.main import create_app
from src.models.gallery import Gallery
from tests.contract.harness import IMPLEMENTED
from tests.contract.shape import CannotCompare, compare_operation

LIST = "/v1/fr/commercial/galleries"
ONE = "/v1/fr/commercial/galleries/{galleryId}"


class GalleryPage(BaseModel):
    # Ninguno obligatorio: el esquema inline del contrato no declara `required`.
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


@pytest.mark.parametrize("method,path", sorted(IMPLEMENTED))
def test_every_implemented_operation_matches_the_contract(method, path):
    generated = create_app(Settings(mongodb_uri="mongodb://unused.invalid:27017/unused")).openapi()

    assert compare_operation(generated, method, path) == []


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


def test_control_a_response_model_as_open_as_the_contract():
    assert compare_operation(get_gallery_with(Gallery), "get", ONE) == []


def test_control_a_response_model_that_lets_extra_fields_through_is_red():
    # El contrato de hoy no cierra `Gallery`; un modelo con extra='allow'
    # tampoco, pero lo ABRE de forma explícita, y eso ya es otro esquema. Cuando
    # docs#103 cierre el contrato, el modelo del servicio tendrá que pasar a
    # extra='forbid' o esta comparación lo dirá en cada esquema.
    class Allow(Gallery):
        model_config = ConfigDict(extra="allow")

    assert compare_operation(get_gallery_with(Allow), "get", ONE) == [
        "200 application/json $ [additionalProperties]: código=True contrato=None"
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


def test_the_comparison_refuses_request_bodies_until_F3():
    app = FastAPI()

    @app.post(LIST)
    def create(body: Gallery):
        return {}

    with pytest.raises(CannotCompare, match="cuerpos de petición"):
        compare_operation(app.openapi(), "post", LIST)
