"""Translation of service-layer exceptions into HTTP responses."""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import DomainError
from app.market_data.exceptions import (
    DataValidationError,
    InstrumentNotFoundError,
    InvalidIntervalError,
    MarketDataError,
    NoDataFoundError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

logger = logging.getLogger(__name__)


def market_data_http_exception(exc: MarketDataError) -> HTTPException:
    """Map a market-data failure to the HTTP error the API reports for it."""
    if isinstance(exc, InstrumentNotFoundError | NoDataFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message)
    if isinstance(exc, InvalidIntervalError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message)
    if isinstance(exc, DataValidationError):
        return HTTPException(
            status_code=422, detail={"message": exc.message, "failures": exc.failures}
        )
    if isinstance(exc, ProviderRateLimitError):
        headers = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(int(exc.retry_after))
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=exc.message, headers=headers
        )
    if isinstance(exc, ProviderConnectionError | ProviderTimeoutError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream provider failure: {exc.message}",
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message)


STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "unprocessable",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
}


def error_body(status_code: int, detail: Any, code: str | None = None) -> dict[str, Any]:
    """The one error shape of the API: a human ``detail`` and a stable ``code``."""
    return {"detail": detail, "code": code or STATUS_CODES.get(status_code, "error")}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.status_code, exc.message, exc.code),
            headers=headers,
        )

    @app.exception_handler(MarketDataError)
    async def handle_market_data_error(_: Request, exc: MarketDataError) -> JSONResponse:
        http_exc = market_data_http_exception(exc)
        return JSONResponse(
            status_code=http_exc.status_code,
            content=error_body(http_exc.status_code, http_exc.detail),
            headers=http_exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.status_code, exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(422, jsonable_encoder(exc.errors()), "validation_error"),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content=error_body(500, "Internal server error"))
