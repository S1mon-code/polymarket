"""Position and inventory management for the market maker."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Position state for a single token."""
    token_id: str
    size: float = 0.0         # Net token quantity (positive = long)
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    total_bought: float = 0.0  # Cumulative bought quantity
    total_sold: float = 0.0    # Cumulative sold quantity
    cost_basis: float = 0.0    # Total cost spent acquiring current position

    @property
    def notional_value(self) -> float:
        """Notional value of the position at average entry."""
        return abs(self.size * self.avg_entry_price)


@dataclass
class MarketPosition:
    """Combined YES/NO position for a single market (condition_id)."""
    condition_id: str
    yes_token_id: str
    no_token_id: str
    yes_position: Position = field(default=None)  # type: ignore[assignment]
    no_position: Position = field(default=None)    # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.yes_position is None:
            self.yes_position = Position(token_id=self.yes_token_id)
        if self.no_position is None:
            self.no_position = Position(token_id=self.no_token_id)


class InventoryManager:
    """Tracks positions, calculates skew, and monitors PnL across all markets."""

    def __init__(self, max_position_per_market: float = 500.0, max_total_exposure: float = 5000.0):
        self.max_position_per_market: float = max_position_per_market
        self.max_total_exposure: float = max_total_exposure
        self._positions: dict[str, Position] = {}          # token_id -> Position
        self._markets: dict[str, MarketPosition] = {}      # condition_id -> MarketPosition

    def register_market(self, condition_id: str, yes_token_id: str, no_token_id: str) -> None:
        """Register a market so we can track YES/NO together."""
        if condition_id not in self._markets:
            market_pos = MarketPosition(
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )
            self._markets[condition_id] = market_pos
            self._positions[yes_token_id] = market_pos.yes_position
            self._positions[no_token_id] = market_pos.no_position
            logger.info("Registered market %s (YES=%s, NO=%s)", condition_id[:12], yes_token_id[:12], no_token_id[:12])

    def get_position(self, token_id: str) -> Position:
        """Get current position for a token. Creates a new one if not found."""
        if token_id not in self._positions:
            self._positions[token_id] = Position(token_id=token_id)
        return self._positions[token_id]

    def update_position(self, token_id: str, side: str, size: float, price: float) -> None:
        """
        Update position after a fill.

        Args:
            token_id: The token that was traded.
            side: "BUY" or "SELL".
            size: Quantity filled (always positive).
            price: Fill price.
        """
        pos = self.get_position(token_id)

        if side == "BUY":
            # Adding to position
            new_size = pos.size + size
            new_cost = pos.cost_basis + (size * price)
            pos.size = new_size
            pos.cost_basis = new_cost
            pos.avg_entry_price = new_cost / new_size if new_size > 0 else 0.0
            pos.total_bought += size
        elif side == "SELL":
            # Reducing position
            if pos.size > 0:
                # Realize PnL on the portion being sold
                pnl_per_unit = price - pos.avg_entry_price
                realized = pnl_per_unit * min(size, pos.size)
                pos.realized_pnl += realized

            new_size = pos.size - size
            if new_size <= 0:
                pos.cost_basis = 0.0
                pos.avg_entry_price = 0.0
                pos.size = max(new_size, 0.0)
            else:
                pos.cost_basis = new_size * pos.avg_entry_price
                pos.size = new_size
            pos.total_sold += size
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'BUY' or 'SELL'.")

        logger.debug(
            "Position updated: token=%s side=%s size=%.2f price=%.4f -> net=%.2f avg=%.4f",
            token_id[:12], side, size, price, pos.size, pos.avg_entry_price,
        )

    def get_skew(self, condition_id: str) -> float:
        """
        Calculate inventory skew for a market.

        skew = (yes_position - no_position) / max_position
        Range: [-1, 1] where -1 = all NO, +1 = all YES.

        Used by the strategy to adjust quotes: if holding too much YES,
        the strategy should lower YES bids and raise YES asks to reduce exposure.
        """
        market = self._markets.get(condition_id)
        if market is None:
            return 0.0

        yes_size = market.yes_position.size
        no_size = market.no_position.size

        if self.max_position_per_market == 0:
            return 0.0

        skew = (yes_size - no_size) / self.max_position_per_market
        return max(-1.0, min(1.0, skew))  # Clamp to [-1, 1]

    def get_pnl(self, condition_id: str) -> float:
        """
        Calculate total realized PnL for a market (YES + NO sides).

        Note: Unrealized PnL requires current market prices, which would be
        calculated by the strategy layer using orderbook mid-prices.
        """
        market = self._markets.get(condition_id)
        if market is None:
            return 0.0
        return market.yes_position.realized_pnl + market.no_position.realized_pnl

    def get_unrealized_pnl(self, condition_id: str, yes_mid: Optional[float], no_mid: Optional[float]) -> float:
        """
        Calculate unrealized PnL for a market given current mid-prices.

        Args:
            condition_id: The market condition ID.
            yes_mid: Current mid-price for the YES token.
            no_mid: Current mid-price for the NO token.
        """
        market = self._markets.get(condition_id)
        if market is None:
            return 0.0

        unrealized = 0.0
        if yes_mid is not None and market.yes_position.size > 0:
            unrealized += (yes_mid - market.yes_position.avg_entry_price) * market.yes_position.size
        if no_mid is not None and market.no_position.size > 0:
            unrealized += (no_mid - market.no_position.avg_entry_price) * market.no_position.size

        return unrealized

    def get_total_exposure(self) -> float:
        """Total capital deployed across all markets (sum of all position notional values)."""
        return sum(pos.notional_value for pos in self._positions.values())

    def get_market_exposure(self, condition_id: str) -> float:
        """Total exposure for a specific market."""
        market = self._markets.get(condition_id)
        if market is None:
            return 0.0
        return market.yes_position.notional_value + market.no_position.notional_value

    def can_increase_position(self, condition_id: str, additional_size: float, price: float) -> bool:
        """
        Check if a new order would violate position or exposure limits.

        Args:
            condition_id: Market to check.
            additional_size: Size of the potential new order.
            price: Price of the potential new order.
        """
        market_exposure = self.get_market_exposure(condition_id)
        additional_notional = additional_size * price

        # Check per-market limit
        if market_exposure + additional_notional > self.max_position_per_market:
            return False

        # Check total exposure limit
        total_exposure = self.get_total_exposure()
        if total_exposure + additional_notional > self.max_total_exposure:
            return False

        return True
