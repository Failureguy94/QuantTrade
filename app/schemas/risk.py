"""Schemas for risk reports."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.risk.metrics import RiskMethod


class RiskReportCreate(BaseModel):
    portfolio_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    method: RiskMethod = RiskMethod.HISTORICAL
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    horizon_days: int = Field(
        default=1, ge=1, le=250, description="Horizon in return periods (trading days)"
    )
    lookback_days: int = Field(
        default=252, ge=30, le=2520, description="Return observations to use"
    )
    simulations: int = Field(default=10_000, ge=1_000, le=200_000)
    random_seed: int | None = Field(
        default=None,
        ge=0,
        le=2**32 - 1,
        description="Monte Carlo seed. Generated and recorded when omitted.",
    )

    @model_validator(mode="after")
    def exactly_one_subject(self) -> "RiskReportCreate":
        if (self.portfolio_id is None) == (self.experiment_id is None):
            raise ValueError("Provide exactly one of portfolio_id or experiment_id")
        return self


class RiskReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    portfolio_id: uuid.UUID | None
    experiment_id: uuid.UUID | None
    method: str
    configuration: dict[str, Any]
    results: dict[str, Any]
    created_at: datetime
