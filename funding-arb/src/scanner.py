"""Funding Rate Scanner — scans exchanges for profitable arbitrage opportunities."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

# Path setup for shared utilities
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Import exchange base — graceful fallback if Agent F's code isn't ready
try:
    from src.exchanges.base import BaseExchange, FundingRate
except ImportError:
    BaseExchange = None  # type: ignore[assignment,misc]
    FundingRate = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Opportunity:
    """A single-exchange funding rate arbitrage opportunity."""

    symbol: str
    exchange: str
    funding_rate: float
    annualized_rate: float
    next_funding_time: float
    spot_price: float
    futures_price: float
    basis: float  # (futures - spot) / spot
    score: float  # composite ranking score

    def __repr__(self) -> str:
        return (
            f"Opportunity({self.symbol} on {self.exchange} | "
            f"rate={self.funding_rate:.4%} | ann={self.annualized_rate:.2%} | "
            f"basis={self.basis:.4%} | score={self.score:.4f})"
        )


@dataclass
class CrossExchangeOpportunity:
    """Cross-exchange funding rate differential opportunity."""

    symbol: str
    long_exchange: str  # where to buy spot
    short_exchange: str  # where to short perp
    rate_differential: float
    annualized_return: float
    long_rate: float = 0.0
    short_rate: float = 0.0

    def __repr__(self) -> str:
        return (
            f"CrossExOpp({self.symbol} | long@{self.long_exchange} "
            f"short@{self.short_exchange} | diff={self.rate_differential:.4%} | "
            f"ann={self.annualized_return:.2%})"
        )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
class FundingScanner:
    """Scans exchanges for funding rate arbitrage opportunities."""

    # Weights for composite score
    _WEIGHT_RATE = 0.5
    _WEIGHT_BASIS = 0.2
    _WEIGHT_CONSISTENCY = 0.3

    def __init__(
        self,
        exchanges: dict[str, "BaseExchange"],
        min_rate: float = 0.0001,
        existing_symbols: set[str] | None = None,
    ) -> None:
        """
        Args:
            exchanges: {name: exchange_instance} dict.
            min_rate: minimum per-interval rate to consider (0.01% = ~10% ann).
            existing_symbols: symbols already in a position (skip during filter).
        """
        self.exchanges = exchanges
        self.min_rate = min_rate
        self.existing_symbols: set[str] = existing_symbols or set()
        # Rate history for consistency checks: {(exchange, symbol): [rate, ...]}
        self._rate_history: dict[tuple[str, str], list[float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def scan_all(self) -> list[Opportunity]:
        """Scan all exchanges for single-exchange opportunities, sorted by score."""
        tasks = []
        for name, exchange in self.exchanges.items():
            tasks.append(self._scan_exchange(name, exchange))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        opportunities: list[Opportunity] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Exchange scan failed: %s", result)
                continue
            opportunities.extend(result)

        opportunities.sort(key=lambda o: o.score, reverse=True)
        return opportunities

    async def scan_cross_exchange(self) -> list[CrossExchangeOpportunity]:
        """Find cross-exchange funding rate differentials."""
        # Collect rates from all exchanges keyed by symbol
        rates_by_symbol: dict[str, list[FundingRate]] = {}

        for name, exchange in self.exchanges.items():
            try:
                rates = await exchange.get_all_funding_rates()
                for fr in rates:
                    rates_by_symbol.setdefault(fr.symbol, []).append(fr)
            except Exception as exc:
                logger.error("Failed to get rates from %s: %s", name, exc)

        opportunities: list[CrossExchangeOpportunity] = []

        for symbol, rates in rates_by_symbol.items():
            if len(rates) < 2:
                continue
            # Sort by rate — highest first
            rates.sort(key=lambda r: r.rate, reverse=True)
            highest = rates[0]
            lowest = rates[-1]
            diff = highest.rate - lowest.rate

            if diff < self.min_rate:
                continue

            # Annualize based on the shorter interval
            interval = min(highest.interval_hours, lowest.interval_hours)
            annualized = diff * (8760 / interval) if interval > 0 else 0.0

            opportunities.append(
                CrossExchangeOpportunity(
                    symbol=symbol,
                    long_exchange=lowest.exchange,
                    short_exchange=highest.exchange,
                    rate_differential=diff,
                    annualized_return=annualized,
                    long_rate=lowest.rate,
                    short_rate=highest.rate,
                )
            )

        opportunities.sort(key=lambda o: o.annualized_return, reverse=True)
        return opportunities

    def filter_opportunities(
        self, opportunities: list[Opportunity]
    ) -> list[Opportunity]:
        """Filter for actionable opportunities only."""
        filtered: list[Opportunity] = []
        for opp in opportunities:
            # Rate above threshold
            if opp.funding_rate < self.min_rate:
                continue

            # Not already in position
            key = f"{opp.exchange}:{opp.symbol}"
            if key in self.existing_symbols or opp.symbol in self.existing_symbols:
                continue

            # Basis sanity — futures shouldn't be >5% away from spot
            if abs(opp.basis) > 0.05:
                logger.debug(
                    "Skipping %s — basis %.4f%% too wide", opp.symbol, opp.basis * 100
                )
                continue

            # Rate consistency — must have been positive at least 3 of last 5 readings
            history = self._rate_history.get((opp.exchange, opp.symbol), [])
            if len(history) >= 3:
                positive_count = sum(1 for r in history[-5:] if r > 0)
                if positive_count < 3:
                    logger.debug(
                        "Skipping %s — rate not consistently positive (%d/5)",
                        opp.symbol,
                        positive_count,
                    )
                    continue

            filtered.append(opp)

        return filtered

    def update_existing_symbols(self, symbols: set[str]) -> None:
        """Update the set of symbols already in a position."""
        self.existing_symbols = symbols

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _scan_exchange(
        self, name: str, exchange: "BaseExchange"
    ) -> list[Opportunity]:
        """Scan a single exchange for opportunities."""
        opportunities: list[Opportunity] = []
        try:
            funding_rates = await exchange.get_all_funding_rates()
        except Exception as exc:
            logger.error("Failed to fetch funding rates from %s: %s", name, exc)
            return []

        for fr in funding_rates:
            if fr.rate < self.min_rate:
                continue

            # Track history
            key = (name, fr.symbol)
            self._rate_history.setdefault(key, [])
            self._rate_history[key].append(fr.rate)
            # Keep last 50 readings
            if len(self._rate_history[key]) > 50:
                self._rate_history[key] = self._rate_history[key][-50:]

            # Get prices for basis calculation
            try:
                spot_price = await exchange.get_spot_price(fr.symbol)
                futures_price = await exchange.get_futures_price(fr.symbol)
            except Exception as exc:
                logger.warning(
                    "Failed to get prices for %s on %s: %s", fr.symbol, name, exc
                )
                continue

            if spot_price <= 0:
                continue

            basis = (futures_price - spot_price) / spot_price
            score = self._compute_score(fr, basis)

            opportunities.append(
                Opportunity(
                    symbol=fr.symbol,
                    exchange=name,
                    funding_rate=fr.rate,
                    annualized_rate=fr.annualized_rate,
                    next_funding_time=fr.next_funding_time,
                    spot_price=spot_price,
                    futures_price=futures_price,
                    basis=basis,
                    score=score,
                )
            )

        logger.info("Found %d opportunities on %s", len(opportunities), name)
        return opportunities

    def _compute_score(self, fr: "FundingRate", basis: float) -> float:
        """Compute a composite score for ranking opportunities."""
        # Normalize annualized rate (cap at 100% for scoring)
        rate_score = min(fr.annualized_rate / 1.0, 1.0)

        # Basis score — prefer small positive basis (contango)
        if 0 < basis < 0.01:
            basis_score = 1.0
        elif 0 <= basis < 0.03:
            basis_score = 0.7
        else:
            basis_score = max(0.0, 1.0 - abs(basis) * 10)

        # Consistency score
        history = self._rate_history.get((fr.exchange, fr.symbol), [])
        if len(history) >= 3:
            positive_ratio = sum(1 for r in history[-10:] if r > 0) / min(
                len(history), 10
            )
            consistency_score = positive_ratio
        else:
            consistency_score = 0.5  # neutral if no history

        return (
            self._WEIGHT_RATE * rate_score
            + self._WEIGHT_BASIS * basis_score
            + self._WEIGHT_CONSISTENCY * consistency_score
        )
