from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.core.logger import get_logger

logger = get_logger(__name__)


class ErrorReportException(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: str | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.details = details


def setup_exception_handlers(app: FastAPI):
    @app.exception_handler(ErrorReportException)
    async def error_report_exception_handler(
        request: Request, exc: ErrorReportException
    ):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": exc.message,
                    "details": exc.details,
                    "path": request.url.path,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        logger.warning(f"Validation error on {request.url.path}: {exc.errors()}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "message": "Validation Error",
                    "details": exc.errors(),
                    "path": request.url.path,
                }
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            f"Unhandled exception on {request.url.path}: {str(exc)}", exc_info=True
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {"message": "Internal Server Error", "path": request.url.path}
            },
        )
