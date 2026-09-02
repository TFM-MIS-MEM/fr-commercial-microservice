from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.api import health
from src.core.exceptions import setup_exception_handlers
from src.core.logger import get_logger, setup_logger
from src.core.rate_limit import limiter
from src.core.settings import settings

logger = get_logger(__name__)


class DBContext:
    client: AsyncIOMotorClient | None = None


db_context = DBContext()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup configurations
    setup_logger(debug=settings.debug)
    logger.info("Connecting to MongoDB", uri=settings.mongo_uri)
    try:
        db_context.client = AsyncIOMotorClient(settings.mongo_uri)
        # Verify connection
        await db_context.client.admin.command("ping")
        logger.info("Successfully connected to MongoDB")
    except Exception as e:
        logger.error("Error connecting to MongoDB", error=str(e), exc_info=True)
        raise e

    yield

    # Shutdown configurations
    logger.info("Disconnecting from MongoDB")
    if db_context.client:
        db_context.client.close()
        logger.info("Successfully disconnected from MongoDB")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.project_name,
        version=settings.version,
        debug=settings.debug,
        openapi_url=f"{settings.api_v1_str}/openapi.json",
        lifespan=lifespan,
    )

    # Configuración de Rate Limit
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # Configuración CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Manejadores de Excepciones (Patrón Error Report)
    setup_exception_handlers(app)

    # Rutas
    app.include_router(health.router, prefix=settings.api_v1_str)

    # Aquí puedes incluir otros routers como models, inference, etc.
    # app.include_router(inference.router, prefix=settings.api_v1_str)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=settings.debug)  # nosec B104
