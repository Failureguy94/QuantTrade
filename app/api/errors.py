"""Translation of service-layer exceptions into HTTP responses."""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

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


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(MarketDataError)
    async def handle_market_data_error(_: Request, exc: MarketDataError) -> JSONResponse:
        http_exc = market_data_http_exception(exc)
        return JSONResponse(
            status_code=http_exc.status_code,
            content={"detail": http_exc.detail},
            headers=http_exc.headers,
        )
