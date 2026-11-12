"""Market Data Engine foundation for Quant."""

from app.market_data.base import MarketDataProvider
from app.market_data.calendar import (
    BSECalendar,
    ExchangeCalendar,
    IndiaCalendar,
    NASDAQCalendar,
    NSECalendar,
    NYSECalendar,
    USCalendar,
    get_calendar,
)
from app.market_data.exceptions import (
    DataValidationError,
    InstrumentNotFoundError,
    InvalidIntervalError,
    MarketDataError,
    NoDataFoundError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.market_data.fingerprint import compute_dataset_fingerprint
from app.market_data.repository import MarketDataRepository, SQLMarketDataRepository
from app.market_data.seed import seed_market_reference_data
from app.market_data.service import MarketDataService
from app.market_data.types import (
    DataInterval,
    OHLCVRecord,
    PriceAdjustmentMode,
    ProviderCapabilities,
)
from app.market_data.validator import MarketDataValidator, ValidationReport
from app.market_data.yahoo import YahooFinanceProvider

__all__ = [
    "MarketDataProvider",
    "YahooFinanceProvider",
    "MarketDataRepository",
    "SQLMarketDataRepository",
    "MarketDataService",
    "MarketDataValidator",
    "ValidationReport",
    "ExchangeCalendar",
    "NSECalendar",
    "BSECalendar",
    "NASDAQCalendar",
    "NYSECalendar",
    "IndiaCalendar",
    "USCalendar",
    "get_calendar",
    "compute_dataset_fingerprint",
    "seed_market_reference_data",
    "DataInterval",
    "PriceAdjustmentMode",
    "ProviderCapabilities",
    "OHLCVRecord",
    "MarketDataError",
    "ProviderConnectionError",
    "ProviderTimeoutError",
    "ProviderRateLimitError",
    "InstrumentNotFoundError",
    "InvalidIntervalError",
    "NoDataFoundError",
    "DataValidationError",
]
