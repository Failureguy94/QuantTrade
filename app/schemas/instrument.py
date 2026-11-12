import uuid

from pydantic import BaseModel, ConfigDict


class InstrumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    name: str
    asset_type: str
    currency_code: str
    exchange_id: uuid.UUID
    is_active: bool
