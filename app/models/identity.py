import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Exchange(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "exchanges"
    __table_args__ = (UniqueConstraint("mic", name="uq_exchanges_mic"),)

    mic: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    calendar_code: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    instruments: Mapped[list["Instrument"]] = relationship(back_populates="exchange")


class Instrument(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange_id", "symbol", name="uq_instruments_exchange_symbol"),
        Index("ix_instruments_asset_type", "asset_type"),
    )

    exchange_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchanges.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, default="EQUITY")
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    exchange: Mapped[Exchange] = relationship(back_populates="instruments")
    provider_symbols: Mapped[list["ProviderSymbol"]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan"
    )


class ProviderSymbol(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "provider_symbols"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_symbol", name="uq_provider_symbols_provider_symbol"
        ),
        Index("ix_provider_symbols_instrument_id", "instrument_id"),
    )

    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_symbol: Mapped[str] = mapped_column(String(100), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    instrument: Mapped[Instrument] = relationship(back_populates="provider_symbols")
