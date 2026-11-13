"""Rate-limit dependencies for routes. The counters are in ``app.core.ratelimit``."""

from collections.abc import Callable

from fastapi import Depends, Request

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.ratelimit import RateLimitExceeded, limiter
from app.models.identity import User


def client_ip(request: Request) -> str:
    """Address of the caller.

    Behind a reverse proxy, run uvicorn with ``--proxy-headers`` so this is the
    forwarded client address rather than the proxy's.
    """
    return request.client.host if request.client else "unknown"


def limit_by_ip(scope: str, per_minute: Callable[[], int]) -> Callable:
    """Limit a route per client address. For routes that do not require a user."""

    async def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        wait = limiter.hit(f"{scope}:ip:{client_ip(request)}", per_minute(), 60.0)
        if wait:
            raise RateLimitExceeded(wait)

    return dependency


def limit_by_user(scope: str, per_minute: Callable[[], int]) -> Callable:
    """Limit a route per signed-in user."""

    async def dependency(current_user: User = Depends(get_current_user)) -> None:
        if not settings.rate_limit_enabled:
            return
        wait = limiter.hit(f"{scope}:user:{current_user.id}", per_minute(), 60.0)
        if wait:
            raise RateLimitExceeded(wait)

    return dependency


# Limits are read from settings on each request so they can be tuned (and tested)
# without rebuilding the routes.
auth_rate_limit = limit_by_ip("auth", lambda: settings.rate_limit_auth_per_minute)
backtest_rate_limit = limit_by_user("backtests", lambda: settings.rate_limit_backtests_per_minute)
risk_rate_limit = limit_by_user("risk", lambda: settings.rate_limit_risk_per_minute)
market_data_rate_limit = limit_by_user(
    "market-data", lambda: settings.rate_limit_market_data_per_minute
)
