"""Strategy types and data structures for the market maker bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(str, Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class TokenSide(str, Enum):
    """Token outcome side."""
    YES = "YES"
    NO = "NO"


@dataclass
class StrategyConfig:
    """Configuration for the band market-making strategy."""
    num_bands: int = 3
    band_width: float = 0.02
    min_spread: float = 0.03
    max_position_per_market: float = 500.0
    max_total_exposure: float = 5000.0
    refresh_interval_seconds: int = 30
    inventory_skew_factor: float = 0.5
    order_size_base: float = 50.0
    order_size_multiplier: list[float] = field(default_factory=lambda: [1.0, 1.5, 2.0])

    @classmethod
    def from_dict(cls, data: dict) -> StrategyConfig:
        """Create config from a dictionary (e.g. loaded from JSON)."""
        return cls(
            num_bands=data.get("num_bands", 3),
            band_width=data.get("band_width", 0.02),
            min_spread=data.get("min_spread", 0.03),
            max_position_per_market=data.get("max_position_per_market", 500.0),
            max_total_exposure=data.get("max_total_exposure", 5000.0),
            refresh_interval_seconds=data.get("refresh_interval_seconds", 30),
            inventory_skew_factor=data.get("inventory_skew_factor", 0.5),
            order_size_base=data.get("order_size_base", 50.0),
            order_size_multiplier=data.get("order_size_multiplier", [1.0, 1.5, 2.0]),
        )


@dataclass
class Order:
    """A single order to be placed on the CLOB."""
    token_id: str
    price: float
    size: float
    side: Side
    token_side: TokenSide  # YES or NO token

    def __post_init__(self) -> None:
        """Validate order constraints."""
        if not 0.01 <= self.price <= 0.99:
            raise ValueError(f"Price {self.price} out of Polymarket range [0.01, 0.99]")
        if self.size <= 0:
            raise ValueError(f"Size must be positive, got {self.size}")


@dataclass
class Market:
    """Representation of a Polymarket prediction market."""
    condition_id: str
    question: str
    token_ids: dict[str, str]  # {"YES": token_id, "NO": token_id}
    end_date: datetime
    volume_24h: float = 0.0
    liquidity: float = 0.0
    active: bool = True
    flagged: bool = False

    @property
    def yes_token_id(self) -> str:
        """Get the YES token ID."""
        return self.token_ids["YES"]

    @property
    def no_token_id(self) -> str:
        """Get the NO token ID."""
        return self.token_ids["NO"]
