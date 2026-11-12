from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    experiments,
    health,
    instruments,
    market_data,
    portfolios,
    risk,
    strategies,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(instruments.router, prefix="/instruments", tags=["instruments"])
api_router.include_router(market_data.router, prefix="/market-data", tags=["market-data"])
api_router.include_router(strategies.router, prefix="/strategies", tags=["strategies"])
api_router.include_router(experiments.router, prefix="/experiments", tags=["experiments"])
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["portfolios"])
api_router.include_router(risk.router, prefix="/risk", tags=["risk"])
