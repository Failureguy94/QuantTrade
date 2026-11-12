import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Strategy(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "strategies"
    __table_args__ = (Index("ix_strategies_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)


class StrategyVersion(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        Index("ix_strategy_versions_strategy_version", "strategy_id", "version"),
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    code_version: Mapped[str | None] = mapped_column(String(100))


class Experiment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiments_user_created", "user_id", "created_at"),
        Index("ix_experiments_status", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="RESTRICT")
    )
    experiment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    data_fingerprint: Mapped[str | None] = mapped_column(String(255))
    engine_version: Mapped[str] = mapped_column(String(50), nullable=False, default="0.1.0")
    random_seed: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)
    results: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationJob(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "simulation_jobs"
    __table_args__ = (Index("ix_simulation_jobs_status_created", "status", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="SET NULL")
    )
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_reference: Mapped[str | None] = mapped_column(String(500))
    error_message: Mapped[str | None] = mapped_column(Text)


class RiskReport(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "risk_reports"
    __table_args__ = (Index("ix_risk_reports_created", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="SET NULL")
    )
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="SET NULL")
    )
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    results: Mapped[dict] = mapped_column(JSONB, nullable=False)
