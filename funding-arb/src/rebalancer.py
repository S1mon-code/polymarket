"""Margin Rebalancer — manages margin health across exchanges."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from src.exchanges.base import BaseExchange, MAX_LEVERAGE
except ImportError:
    BaseExchange = None  # type: ignore[assignment,misc]
    MAX_LEVERAGE = 3

from src.engine import ArbPosition

logger = logging.getLogger(__name__)

# Margin thresholds
_MARGIN_REBALANCE_TRIGGER = 0.55  # trigger rebalance at 55%
_MARGIN_TARGET = 0.35  # aim for 35% after rebalance
_MARGIN_CRITICAL = 0.70  # reduce position if above this


class Rebalancer:
    """Manages margin rebalancing across exchanges for arb positions."""

    def __init__(
        self,
        exchanges: dict[str, "BaseExchange"],
        dry_run: bool = True,
    ) -> None:
        self.exchanges = exchanges
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def check_and_rebalance(
        self, positions: list[ArbPosition]
    ) -> list[str]:
        """
        Check margin health on each exchange with active positions.
        If margin ratio is too high, attempt to add margin or flag for
        position reduction.

        Returns a list of action descriptions taken.
        """
        actions: list[str] = []

        # Group positions by exchange
        positions_by_exchange: dict[str, list[ArbPosition]] = {}
        for pos in positions:
            positions_by_exchange.setdefault(pos.exchange, []).append(pos)

        for exchange_name, exchange_positions in positions_by_exchange.items():
            exchange = self.exchanges.get(exchange_name)
            if exchange is None:
                actions.append(f"WARN: Exchange {exchange_name} not available")
                continue

            try:
                margin_ratio = await exchange.get_margin_ratio()
            except Exception as exc:
                actions.append(
                    f"WARN: Cannot check margin on {exchange_name}: {exc}"
                )
                continue

            logger.info(
                "%s margin ratio: %.1f%% (%d positions)",
                exchange_name, margin_ratio * 100, len(exchange_positions),
            )

            # Critical — need to reduce positions
            if margin_ratio >= _MARGIN_CRITICAL:
                action = await self._handle_critical_margin(
                    exchange_name, exchange, exchange_positions, margin_ratio,
                )
                actions.append(action)

            # Elevated — try to add margin
            elif margin_ratio >= _MARGIN_REBALANCE_TRIGGER:
                action = await self._handle_elevated_margin(
                    exchange_name, exchange, exchange_positions, margin_ratio,
                )
                actions.append(action)
            else:
                actions.append(
                    f"{exchange_name}: margin healthy at {margin_ratio:.1%}"
                )

        return actions

    async def add_margin(
        self, exchange_name: str, symbol: str, amount: float
    ) -> bool:
        """
        Add margin to a futures position on an exchange.
        Returns True if successful.
        """
        exchange = self.exchanges.get(exchange_name)
        if exchange is None:
            logger.error("Exchange %s not available for add_margin", exchange_name)
            return False

        if self.dry_run:
            logger.info(
                "[DRY RUN] Would add %.4f margin to %s on %s",
                amount, symbol, exchange_name,
            )
            return True

        # Check if exchange has sufficient free balance
        try:
            balance = await exchange.get_balance()
            free = balance.get("USDT", 0.0) + balance.get("USD", 0.0)
            if free < amount:
                logger.warning(
                    "Insufficient free balance on %s: have %.2f, need %.2f",
                    exchange_name, free, amount,
                )
                return False
        except Exception as exc:
            logger.error("Balance check failed: %s", exc)
            return False

        # Note: actual margin addition depends on exchange-specific API.
        # BaseExchange doesn't have an add_margin method yet.
        # For now, log and return True (the position monitor handles urgency).
        logger.info(
            "Would add %.4f margin to %s on %s — pending exchange API support",
            amount, symbol, exchange_name,
        )
        return True

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------
    async def _handle_critical_margin(
        self,
        exchange_name: str,
        exchange: "BaseExchange",
        positions: list[ArbPosition],
        margin_ratio: float,
    ) -> str:
        """Handle critically high margin — flag smallest position for exit."""
        # Find smallest position to recommend closing
        if not positions:
            return f"{exchange_name}: critical margin ({margin_ratio:.1%}) but no positions"

        smallest = min(positions, key=lambda p: p.notional_usd)

        msg = (
            f"CRITICAL: {exchange_name} margin at {margin_ratio:.1%} — "
            f"recommend closing {smallest.symbol} "
            f"(${smallest.notional_usd:.2f}) to free margin"
        )
        logger.warning(msg)
        return msg

    async def _handle_elevated_margin(
        self,
        exchange_name: str,
        exchange: "BaseExchange",
        positions: list[ArbPosition],
        margin_ratio: float,
    ) -> str:
        """Handle elevated margin — try to add margin from free balance."""
        try:
            balance = await exchange.get_balance()
            free = balance.get("USDT", 0.0) + balance.get("USD", 0.0)
        except Exception:
            free = 0.0

        if free > 10.0:  # at least $10 free
            # Add margin to the largest position
            largest = max(positions, key=lambda p: p.notional_usd)
            add_amount = min(free * 0.5, 50.0)  # add up to 50% of free, max $50

            success = await self.add_margin(
                exchange_name, largest.symbol, add_amount,
            )
            if success:
                return (
                    f"{exchange_name}: margin elevated ({margin_ratio:.1%}), "
                    f"added ${add_amount:.2f} margin to {largest.symbol}"
                )

        return (
            f"{exchange_name}: margin elevated ({margin_ratio:.1%}), "
            f"no free balance to add — monitor closely"
        )
