"""Documentación servida desde el contrato, no desde el código (criterio de C2).

`/openapi.json` devuelve el contenido de `openapi/fr-commercial.yaml` y `/docs`
lo presenta. FastAPI NO sirve la especificación que genera a partir de los
modelos (`openapi_url=None` en `src/main.py`): esa descripción existe solo para
compararla con el contrato en las pruebas, que es lo que obliga a que coincidan.

Las dos rutas quedan fuera de `/v1`: no forman parte de la API versionada, no
aparecen en el contrato y no exigen identidad.
"""

from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

# Resuelta desde este fichero, nunca desde el directorio de trabajo: el proceso
# arranca desde `/app` en la imagen y desde la raíz del repositorio en local.
SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi" / "fr-commercial.yaml"


def load_spec(path: Path = SPEC_PATH) -> dict:
    """Carga el contrato. Si falta, el servicio no arranca.

    `pricing` registra el fallo y sigue sin documentación. Aquí no: el
    `Dockerfile` heredado de la plantilla no copiaba `openapi/`, y un contrato
    ausente de la imagen es un defecto de construcción que tiene que verse al
    arrancar, no en la primera visita a `/docs`.
    """
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


router = APIRouter(include_in_schema=False)


@router.get("/openapi.json")
async def openapi_json(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.spec)


@router.get("/docs")
async def docs(request: Request) -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/openapi.json", title=request.app.state.spec["info"]["title"]
    )
