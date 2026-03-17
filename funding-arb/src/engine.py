"""Decision Engine — determines when to enter/exit funding rate arb positions."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from src.exchanges.base import FundingRate, MAX_LEVERAGE
except ImportError:
    FundingRate = None  # type: ignore[assignment,misc]
    MAX_LEVERAGE = 3

from src.scanner import Opportunity

logger = logging.getLogger(__name__)

# Hard safety limit — NEVER exceed 3x
_ABSOLUTE_MAX_LEVERAGE = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class ArbPosition:
    """Tracks a live funding rate arb position (spot long + perp short)."""

    symbol: str
    exchange: str
    spot_entry_price: float
    futures_entry_price: float
    size: float  # in base asset units
    entry_time: float
    total_funding_earned: float = 0.0
    realized_pnl: float = 0.0
    leverage: int = 1

    @property
    def notional_usd(self) -> float:
        return self.size * self.spot_entry_price

    @property
    def age_hours(self) -> float:
        return (time.time() - self.entry_time) / 3600

    def __repr__(self) -> str:
        return (
            f"ArbPos({self.symbol}@{self.exchange} | "
            f"size={self.size:.6f} | funding={self.total_funding_earned:.4f} | "
            f"pnl={self.realized_pnl:.4f})"
        )


@dataclass
class EntryDecision:
    enter: bool
    reason: str
    size: float
    exchange: str
    symbol: str


@dataclass
class ExitDecision:
    exit: bool
    reason: str
    urgency: str  # "normal", "urgent", "emergency"


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    "min_annualized_rate": 0.12,  # 12%
    "max_positions": 10,
    "position_size_usd": 100.0,
    "max_leverage": 3,
    "exit_rate_threshold": 0.0,  # exit when rate goes negative
    "rate_lookback_hours": 24,
    "max_basis_pct": 0.03,  # 3% max basis divergence
    "loss_limit_pct": 0.05,  # 5% loss → exit
    "rate_velocity_threshold": -0.0002,  # rate declining fast
    "margin_exit_threshold": 0.65,  # exit if margin ratio > 65%
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class ArbEngine:
    """Core decision engine for funding rate arbitrage."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        # Enforce leverage cap
        if self.config["max_leverage"] > _ABSOLUTE_MAX_LEVERAGE:
            logger.warning(
                "Config max_leverage=%d exceeds absolute max %d — clamping",
                self.config["max_leverage"],
                _ABSOLUTE_MAX_LEVERAGE,
            )
            self.config["max_leverage"] = _ABSOLUTE_MAX_LEVERAGE

        # Rate history for velocity checks: {(exchange, symbol): [(timestamp, rate)]}
        self._rate_history: dict[tuple[str, str], list[tuple[float, float]]] = {}

    # ------------------------------------------------------------------
    # Entry decision
    # ------------------------------------------------------------------
    def should_enter(
        self,
        opportunity: Opportunity,
        current_positions: list[ArbPosition],
        available_capital: float | None = None,
    ) -> EntryDecision:
        """Decide whether to enter a new arb position."""
        no_entry = EntryDecision(
            enter=False, reason="", size=0.0,
            exchange=opportunity.exchange, symbol=opportunity.symbol,
        )

        # Max positions check
        if len(current_positions) >= self.config["max_positions"]:
            no_entry.reason = (
                f"At max positions ({self.config['max_positions']})"
            )
            return no_entry

        # Already in this symbol on this exchange?
        for pos in current_positions:
            if pos.symbol == opportunity.symbol and pos.exchange == opportunity.exchange:
                no_entry.reason = f"Already in {opportunity.symbol} on {opportunity.exchange}"
                return no_entry

        # Rate too low
        if opportunity.annualized_rate < self.config["min_annualized_rate"]:
            no_entry.reason = (
                f"Annualized rate {opportunity.annualized_rate:.2%} below "
                f"minimum {self.config['min_annualized_rate']:.2%}"
            )
            return no_entry

        # Basis too wide
        if abs(opportunity.basis) > self.config["max_basis_pct"]:
            no_entry.reason = (
                f"Basis {opportunity.basis:.4%} exceeds max {self.config['max_basis_pct']:.2%}"
            )
            return no_entry

        # Rate consistency — check history
        key = (opportunity.exchange, opportunity.symbol)
        history = self._rate_history.get(key, [])
        lookback_cutoff = time.time() - self.config["rate_lookback_hours"] * 3600
        recent_rates = [r for ts, r in history if ts > lookback_cutoff]
        if len(recent_rates) >= 3:
            positive_count = sum(1 for r in recent_rates if r > 0)
            if positive_count / len(recent_rates) < 0.7:
                no_entry.reason = (
                    f"Rate not consistently positive — "
                    f"{positive_count}/{len(recent_rates)} positive in lookback"
                )
                return no_entry

        # Capital check
        size_usd = self.calculate_position_size(opportunity, available_capital)
        if size_usd <= 0:
            no_entry.reason = "Insufficient capital or invalid position size"
            return no_entry

        # Calculate size in base asset
        size_base = size_usd / opportunity.spot_price if opportunity.spot_price > 0 else 0
        if size_base <= 0:
            no_entry.reason = "Invalid spot price"
            return no_entry

        return EntryDecision(
            enter=True,
            reason=(
                f"Rate={opportunity.annualized_rate:.2%} ann, "
                f"basis={opportunity.basis:.4%}, score={opportunity.score:.4f}"
            ),
            size=size_base,
            exchange=opportunity.exchange,
            symbol=opportunity.symbol,
        )

    # ------------------------------------------------------------------
    # Exit decision
    # ------------------------------------------------------------------
    def should_exit(
        self,
        position: ArbPosition,
        current_rate: "FundingRate | None",
        margin_ratio: float = 0.0,
    ) -> ExitDecision:
        """Decide whether to exit an existing arb position."""
        no_exit = ExitDecision(exit=False, reason="", urgency="normal")

        # Emergency: margin ratio too high
        if margin_ratio > self.config["margin_exit_threshold"]:
            return ExitDecision(
                exit=True,
                reason=f"Margin ratio {margin_ratio:.1%} exceeds threshold {self.config['margin_exit_threshold']:.1%}",
                urgency="emergency",
            )

        # No rate data
        if current_rate is None:
            return ExitDecision(
                exit=True,
                reason="Cannot fetch current funding rate — exiting for safety",
                urgency="urgent",
            )

        # Record rate for velocity tracking
        key = (position.exchange, position.symbol)
        self._rate_history.setdefault(key, [])
        self._rate_history[key].append((time.time(), current_rate.rate))
        # Trim history
        cutoff = time.time() - 72 * 3600  # keep 72h
        self._rate_history[key] = [
            (ts, r) for ts, r in self._rate_history[key] if ts > cutoff
        ]

        # Rate turned negative
        if current_rate.rate < self.config["exit_rate_threshold"]:
            return ExitDecision(
                exit=True,
                reason=f"Funding rate {current_rate.rate:.4%} below exit threshold {self.config['exit_rate_threshold']:.4%}",
                urgency="normal",
            )

        # Rate declining rapidly (velocity check)
        velocity = self._rate_velocity(key)
        if velocity is not None and velocity < self.config["rate_velocity_threshold"]:
            return ExitDecision(
                exit=True,
                reason=f"Rate declining rapidly — velocity={velocity:.6f}/interval",
                urgency="urgent",
            )

        # Position loss check
        if position.notional_usd > 0:
            total_pnl = position.realized_pnl + position.total_funding_earned
            loss_pct = total_pnl / position.notional_usd
            if loss_pct < -self.config["loss_limit_pct"]:
                return ExitDecision(
                    exit=True,
                    reason=f"Position loss {loss_pct:.2%} exceeds limit {-self.config['loss_limit_pct']:.2%}",
                    urgency="urgent",
                )

        return no_exit

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------
    def calculate_position_size(
        self, opportunity: Opportunity, available_capital: float | None = None,
    ) -> float:
        """Calculate position size in USD."""
        base_size = self.config["position_size_usd"]

        # Scale by opportunity quality (up to 1.5x for great opportunities)
        quality_multiplier = 1.0
        if opportunity.annualized_rate > 0.25:  # >25% ann
            quality_multiplier = 1.5
        elif opportunity.annualized_rate > 0.18:  # >18% ann
            quality_multiplier = 1.25

        size = base_size * quality_multiplier

        # Cap by available capital (use at most 20% per position)
        if available_capital is not None:
            max_per_position = available_capital * 0.2
            size = min(size, max_per_position)

        # Must be positive
        return max(size, 0.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _rate_velocity(self, key: tuple[str, str]) -> float | None:
        """Calculate rate of change of funding rate over recent readings."""
        history = self._rate_history.get(key, [])
        if len(history) < 3:
            return None

        recent = history[-6:]  # last 6 readings
        if len(recent) < 2:
            return None

        # Simple linear slope
        rates = [r for _, r in recent]
        n = len(rates)
        x_mean = (n - 1) / 2
        y_mean = sum(rates) / n
        numerator = sum((i - x_mean) * (r - y_mean) for i, r in enumerate(rates))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0
        return numerator / denominator

    def record_rate(self, exchange: str, symbol: str, rate: float) -> None:
        """Manually record a funding rate observation."""
        key = (exchange, symbol)
        self._rate_history.setdefault(key, [])
        self._rate_history[key].append((time.time(), rate))
