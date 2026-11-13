"""Total row counts for list endpoints.

Lists return a plain JSON array. The number of rows matching the filters, ignoring
``limit`` and ``offset``, is reported in the ``X-Total-Count`` response header.
"""

from fastapi import Response
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

TOTAL_COUNT_HEADER = "X-Total-Count"


async def set_total_count(response: Response, db: AsyncSession, stmt: Select) -> None:
    """Count the rows ``stmt`` would return without its limit, offset and ordering."""
    unpaged = stmt.order_by(None).limit(None).offset(None)
    total = await db.scalar(select(func.count()).select_from(unpaged.subquery()))
    response.headers[TOTAL_COUNT_HEADER] = str(total or 0)
