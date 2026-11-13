"""Liveness and readiness.

``/health`` (and ``/health/live``) answers whenever the process is serving requests.
``/health/ready`` (and ``/health/database``) also needs the database, so use it to
decide whether to route traffic here.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter()


@router.get("")
@router.get("/live", include_in_schema=False)
async def health_check():
    return {"status": "ok", "service": "quant-api"}


@router.get("/ready")
@router.get("/database", include_in_schema=False)
async def readiness(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
