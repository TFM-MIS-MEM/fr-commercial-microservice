"""Punto de entrada: `uvicorn --factory src.main:create_app`.

Se construye con una factoría y no en el momento de importar el módulo: la
configuración se valida al crear la aplicación, y las pruebas crean la suya con
la configuración que eligen.

Lo que NO está, y por qué, frente a `python-template`:

  - Sin limitador de peticiones (`slowapi`). El límite general es de la
    pasarela. Aquí contaba por dirección de origen, y detrás de Kong todos los
    clientes comparten la de la pasarela: un único cupo para todo el mundo, cuyo
    `429` además no era `ErrorReport`.
  - Sin `CORSMiddleware`. El navegador nunca habla con este servicio
    directamente, y la plantilla lo montaba con `allow_origins=["*"]` y
    `allow_credentials=True`, la combinación de #69. La política de CORS es de
    la pasarela (TFM-MIS-MEM/docs#102 para `pricing`).
  - Sin prefijo `/api/v1`, que contradice la convención de rutas.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import docs, health
from src.core.database import connect
from src.core.errors import install_error_handlers
from src.core.logger import get_logger, setup_logger
from src.core.security_headers import SecurityHeadersMiddleware
from src.core.settings import Settings, get_settings

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logger(settings.log_level)
    spec = docs.load_spec()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = await connect(settings.mongodb_uri)
        app.state.db = client.get_default_database()
        # Nunca la URI: cuando lleve credencial (#106), la escribiría en el registro.
        logger.info("mongodb_connected", database=app.state.db.name)
        try:
            yield
        finally:
            await client.close()

    app = FastAPI(
        title=spec["info"]["title"],
        version=spec["info"]["version"],
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.spec = spec

    app.add_middleware(SecurityHeadersMiddleware)
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(docs.router)
    return app
