from fastapi import APIRouter, Request

from src.core.rate_limit import limiter
from src.core.settings import settings

router = APIRouter()


@router.get("/health", tags=["system"], summary="Health Check")
@limiter.limit("100/minute")
async def health_check(request: Request):
    return {
        "status": "ok",
        "service": settings.project_name,
        "version": settings.version,
        "environment": settings.environment,
    }
