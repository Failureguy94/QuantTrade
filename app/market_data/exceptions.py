"""Market data engine exceptions with error normalization and retry classification."""

from typing import Any


class MarketDataError(Exception):
    """Base exception for all market data engine operations."""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        is_retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.is_retryable = is_retryable
        self.details = details or {}

    def __str__(self) -> str:
        provider_prefix = f"[{self.provider}] " if self.provider else ""
        return f"{provider_prefix}{self.message}"


class ProviderConnectionError(MarketDataError):
    """Network connection failure communicating with data provider (retryable)."""

    def __init__(
        self,
        message: str = "Connection to market data provider failed",
        provider: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider=provider, is_retryable=True, details=details)


class ProviderTimeoutError(MarketDataError):
    """Request to data provider timed out (retryable)."""

    def __init__(
        self,
        message: str = "Provider request timed out",
        provider: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider=provider, is_retryable=True, details=details)


class ProviderRateLimitError(MarketDataError):
    """Provider rate limit or quota exceeded (retryable with backoff)."""

    def __init__(
        self,
        message: str = "Provider rate limit exceeded",
        provider: str | None = None,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if retry_after is not None:
            merged_details["retry_after"] = retry_after
        super().__init__(message, provider=provider, is_retryable=True, details=merged_details)
        self.retry_after = retry_after


class InstrumentNotFoundError(MarketDataError):
    """Instrument or provider symbol not recognized by provider (non-retryable)."""

    def __init__(
        self,
        message: str = "Instrument not found for provider",
        provider: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider=provider, is_retryable=False, details=details)


class InvalidIntervalError(MarketDataError):
    """Requested bar interval is not supported by provider or engine (non-retryable)."""

    def __init__(
        self,
        interval: str,
        supported_intervals: list[str] | None = None,
        provider: str | None = None,
    ) -> None:
        msg = f"Interval '{interval}' is not supported"
        if supported_intervals:
            msg += f". Supported: {', '.join(supported_intervals)}"
        super().__init__(
            msg,
            provider=provider,
            is_retryable=False,
            details={"interval": interval, "supported_intervals": supported_intervals or []},
        )
        self.interval = interval


class NoDataFoundError(MarketDataError):
    """Provider returned an empty dataset for a valid query range (non-retryable)."""

    def __init__(
        self,
        message: str = "No market data returned for requested range",
        provider: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider=provider, is_retryable=False, details=details)


class DataValidationError(MarketDataError):
    """Market data integrity check failed (e.g. High < Low, negative prices) (non-retryable)."""

    def __init__(
        self,
        message: str,
        failures: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if failures:
            merged_details["failures"] = failures
        super().__init__(
            message, provider=None, is_retryable=False, details=merged_details
        )
        self.failures = failures or []
