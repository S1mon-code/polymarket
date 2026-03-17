"""Abstract base exchange interface for funding rate arbitrage."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_LEVERAGE = 3  # Hard safety limit — never exceed this


@dataclass
class FundingRate:
    symbol: str
    exchange: str
    rate: float  # Current funding rate (e.g., 0.0001 = 0.01%)
    next_funding_time: float  # Unix timestamp
    interval_hours: int  # 1, 4, or 8 hours
    annualized_rate: float  # rate * (8760 / interval_hours)


@dataclass
class Position:
    symbol: str
    exchange: str
    side: str  # "long" or "short"
    size: float
    entry_price: float
    unrealized_pnl: float
    margin: float
    leverage: float


@dataclass
class OrderResult:
    success: bool
    order_id: str
    symbol: str
    side: str
    price: float
    size: float
    error: str | None = None


def _clamp_leverage(leverage: int) -> int:
    """Enforce the hard leverage cap."""
    if leverage > MAX_LEVERAGE:
        logger.warning(
            "Requested leverage %dx exceeds max %dx — clamping",
            leverage,
            MAX_LEVERAGE,
        )
        return MAX_LEVERAGE
    return max(1, leverage)


class BaseExchange(ABC):
    """Unified async interface every exchange adapter must implement."""

    name: str = "base"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

    # ------------------------------------------------------------------
    # Funding rates
    # ------------------------------------------------------------------
    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """Get current funding rate for a symbol."""

    @abstractmethod
    async def get_all_funding_rates(self) -> list[FundingRate]:
        """Get funding rates for all perpetual contracts."""

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------
    @abstractmethod
    async def get_spot_price(self, symbol: str) -> float:
        """Get current spot price."""

    @abstractmethod
    async def get_futures_price(self, symbol: str) -> float:
        """Get current perpetual futures price."""

    # ------------------------------------------------------------------
    # Spot orders
    # ------------------------------------------------------------------
    @abstractmethod
    async def buy_spot(self, symbol: str, size: float) -> OrderResult:
        """Buy spot (market order)."""

    @abstractmethod
    async def sell_spot(self, symbol: str, size: float) -> OrderResult:
        """Sell spot (market order)."""

    # ------------------------------------------------------------------
    # Perpetual futures orders
    # ------------------------------------------------------------------
    @abstractmethod
    async def open_short(
        self, symbol: str, size: float, leverage: int = 1
    ) -> OrderResult:
        """Open short perpetual position (market order)."""

    @abstractmethod
    async def close_short(self, symbol: str, size: float) -> OrderResult:
        """Close short perpetual position."""

    # ------------------------------------------------------------------
    # Account / position queries
    # ------------------------------------------------------------------
    @abstractmethod
    async def get_position(self, symbol: str) -> Position | None:
        """Get current perpetual position."""

    @abstractmethod
    async def get_all_positions(self) -> list[Position]:
        """Get all open positions."""

    @abstractmethod
    async def get_balance(self) -> dict[str, float]:
        """Get account balances {asset: amount}."""

    @abstractmethod
    async def get_margin_ratio(self) -> float:
        """Get current margin ratio (0-1, lower is safer)."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """Clean up exchange connections."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _now() -> float:
        return time.time()

    def __repr__(self) -> str:
        mode = "testnet" if self.testnet else "live"
        return f"<{self.__class__.__name__} ({mode})>"
