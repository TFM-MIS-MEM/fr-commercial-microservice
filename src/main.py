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

import asyncio
from contextlib import asynccontextmanager
from time import perf_counter

import torch
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from src.api import docs, galleries, health, identities, inference, recognition
from src.controllers.inference import Inference
from src.core.database import connect, ensure_indexes
from src.core.errors import install_error_handlers
from src.core.logger import get_logger, setup_logger
from src.core.security_headers import SecurityHeadersMiddleware
from src.core.settings import Settings, get_settings
from src.documents import INDEXES
from src.inference.engine import MODELS, load_engine, versioned

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logger(settings.log_level)
    spec = docs.load_spec()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = await connect(settings.mongodb_uri)
        try:
            app.state.db = client.get_default_database()
            # Nunca la URI: cuando lleve credencial (#106), la escribiría en el registro.
            logger.info("mongodb_connected", database=app.state.db.name)
            # Si falla, no se arranca: el porqué está en `ensure_indexes`.
            await ensure_indexes(app.state.db, INDEXES)
            # Después de los índices. Si falta un fichero de pesos, tampoco se
            # arranca, y mientras se cargan uvicorn no acepta conexiones: /health
            # no responde hasta que los modelos están listos
            # (src/inference/engine.py). Los hilos de torch se fijan aquí para
            # la carga y, de nuevo, en cada hilo de trabajo antes de inferir
            # (src/controllers/inference.py): son estado por hilo.
            if settings.torch_num_threads is not None:
                torch.set_num_threads(settings.torch_num_threads)
            started = perf_counter()
            engine = await run_in_threadpool(load_engine, settings.checkpoints_dir)
            app.state.inference = Inference(
                engine=engine,
                semaphore=asyncio.Semaphore(settings.inference_concurrency),
                token_secret=settings.embedding_token_secret.get_secret_value(),
                torch_threads=settings.torch_num_threads,
            )
            logger.info(
                "models_loaded",
                models=[versioned(m) for m in MODELS],
                seconds=round(perf_counter() - started, 2),
                torch_threads=torch.get_num_threads(),
                inference_concurrency=settings.inference_concurrency,
            )
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
    app.include_router(galleries.router)
    app.include_router(identities.router)
    app.include_router(recognition.router)
    app.include_router(inference.router)
    return app
