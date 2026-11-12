"""Schemas for strategies and their immutable versions."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class StrategyTypeRead(BaseModel):
    type: str
    name: str
    description: str
    default_parameters: dict[str, Any]
    parameter_schema: dict[str, Any]


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    strategy_type: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=5000)
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters for version 1. Omitted values take the type's defaults.",
    )

    _normalize_name = field_validator("name")(_clean_name)


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    is_archived: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None


class StrategyVersionCreate(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class StrategyVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    version: int
    parameters: dict[str, Any]
    code_version: str | None
    created_at: datetime


class StrategyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    strategy_type: str
    description: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    latest_version: StrategyVersionRead | None = None
