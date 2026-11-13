"""Schemas for experiments (backtests) and their job records."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BacktestRequest(BaseModel):
    strategy_version_id: uuid.UUID
    instrument_id: uuid.UUID
    start: date = Field(..., description="First session to include (UTC date)")
    end: date = Field(..., description="Last session to include (UTC date)")
    interval: Literal["1d", "1wk", "1mo"] = "1d"
    adjusted: bool = Field(
        default=True,
        description="Use split- and dividend-adjusted prices (recommended for backtests)",
    )
    initial_capital: Decimal = Field(
        default=Decimal("100000"), gt=0, max_digits=24, decimal_places=8
    )
    commission_bps: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("2"), ge=0, le=1000)
    position_size: Decimal = Field(
        default=Decimal("1"), gt=0, le=1, description="Fraction of equity deployed per entry"
    )
    allow_short: bool = False
    fractional_shares: bool = False
    risk_free_rate: float = Field(default=0.0, ge=-1.0, le=1.0, description="Annual rate")
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Market data provider. Defaults to the one the server is configured with.",
    )

    @model_validator(mode="after")
    def start_before_end(self) -> "BacktestRequest":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class ExperimentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    experiment_type: str
    status: str
    strategy_version_id: uuid.UUID | None
    configuration: dict[str, Any]
    data_fingerprint: str | None
    engine_version: str
    random_seed: int | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    metrics: dict[str, Any] | None = None


class ExperimentRead(ExperimentSummary):
    results: dict[str, Any] | None = None


class SimulationJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    experiment_id: uuid.UUID | None
    job_type: str
    status: str
    progress: int
    result_reference: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
