import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.identity import Instrument
from app.schemas.instrument import InstrumentRead

router = APIRouter()


@router.get("", response_model=list[InstrumentRead])
async def list_instruments(
    exchange_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Instrument).where(Instrument.is_active.is_(True)).limit(limit)
    if exchange_id:
        stmt = stmt.where(Instrument.exchange_id == exchange_id)
    result = await db.execute(stmt)
    return result.scalars().all()
