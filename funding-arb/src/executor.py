"""Trade Executor — handles atomic entry/exit of funding rate arb positions."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Coroutine, Any

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from src.exchanges.base import BaseExchange, OrderResult, MAX_LEVERAGE
except ImportError:
    BaseExchange = None  # type: ignore[assignment,misc]
    OrderResult = None  # type: ignore[assignment,misc]
    MAX_LEVERAGE = 3

from src.engine import ArbPosition, EntryDecision

logger = logging.getLogger(__name__)

# Hard safety limit
_ABSOLUTE_MAX_LEVERAGE = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class ExitResult:
    success: bool
    spot_pnl: float
    funding_earned: float
    total_pnl: float
    reason: str

    def __repr__(self) -> str:
        status = "OK" if self.success else "FAILED"
        return (
            f"ExitResult({status} | spot_pnl={self.spot_pnl:.4f} | "
            f"funding={self.funding_earned:.4f} | total={self.total_pnl:.4f})"
        )


@dataclass
class _SimulatedOrder:
    """Simulated order for dry-run mode."""

    success: bool = True
    order_id: str = "DRY_RUN"
    symbol: str = ""
    side: str = ""
    price: float = 0.0
    size: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------
class TradeExecutor:
    """Executes entry/exit for funding rate arb positions."""

    def __init__(
        self,
        exchanges: dict[str, "BaseExchange"],
        dry_run: bool = True,
        leverage: int = 1,
    ) -> None:
        self.exchanges = exchanges
        self.dry_run = dry_run
        self.leverage = min(leverage, _ABSOLUTE_MAX_LEVERAGE)
        self._positions: list[ArbPosition] = []

    @property
    def positions(self) -> list[ArbPosition]:
        return list(self._positions)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------
    async def enter_position(self, decision: EntryDecision) -> ArbPosition | None:
        """
        Execute entry: buy spot + open short perp simultaneously.
        If one leg fails, immediately unwind the other.
        In DRY_RUN mode: log and return simulated position.
        """
        if not decision.enter:
            logger.warning("enter_position called with enter=False — skipping")
            return None

        exchange_name = decision.exchange
        exchange = self.exchanges.get(exchange_name)
        if exchange is None:
            logger.error("Exchange %s not found", exchange_name)
            return None

        symbol = decision.symbol
        size = decision.size

        logger.info(
            "ENTER %s %s on %s | size=%.6f | dry_run=%s",
            symbol, "arb", exchange_name, size, self.dry_run,
        )

        if self.dry_run:
            return await self._enter_dry_run(decision, exchange)

        # --- Live execution ---
        # 1. Check balance
        try:
            balance = await exchange.get_balance()
            usdt_balance = balance.get("USDT", 0.0) + balance.get("USD", 0.0)
            spot_price = await exchange.get_spot_price(symbol)
            required = size * spot_price
            if usdt_balance < required * 1.05:  # 5% buffer
                logger.error(
                    "Insufficient balance: have %.2f, need %.2f",
                    usdt_balance, required,
                )
                return None
        except Exception as exc:
            logger.error("Balance check failed on %s: %s", exchange_name, exc)
            return None

        # 2. Execute atomic: spot buy + perp short
        leverage = min(self.leverage, _ABSOLUTE_MAX_LEVERAGE)

        spot_result, perp_result = await self._execute_atomic(
            spot_fn=lambda: exchange.buy_spot(symbol, size),
            perp_fn=lambda: exchange.open_short(symbol, size, leverage),
            unwind_spot_fn=lambda: exchange.sell_spot(symbol, size),
            unwind_perp_fn=lambda: exchange.close_short(symbol, size),
        )

        if spot_result is None or perp_result is None:
            return None

        if not spot_result.success or not perp_result.success:
            logger.error("Entry partially failed — should already be unwound")
            return None

        position = ArbPosition(
            symbol=symbol,
            exchange=exchange_name,
            spot_entry_price=spot_result.price,
            futures_entry_price=perp_result.price,
            size=size,
            entry_time=time.time(),
            leverage=leverage,
        )
        self._positions.append(position)
        logger.info("Entered position: %s", position)
        return position

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------
    async def exit_position(self, position: ArbPosition) -> ExitResult:
        """
        Execute exit: sell spot + close short perp simultaneously.
        Handles partial failures with unwind logic.
        """
        exchange = self.exchanges.get(position.exchange)
        if exchange is None:
            return ExitResult(
                success=False, spot_pnl=0, funding_earned=0, total_pnl=0,
                reason=f"Exchange {position.exchange} not found",
            )

        logger.info(
            "EXIT %s on %s | size=%.6f | dry_run=%s",
            position.symbol, position.exchange, position.size, self.dry_run,
        )

        if self.dry_run:
            return await self._exit_dry_run(position, exchange)

        # --- Live execution ---
        spot_result, perp_result = await self._execute_atomic(
            spot_fn=lambda: exchange.sell_spot(position.symbol, position.size),
            perp_fn=lambda: exchange.close_short(position.symbol, position.size),
            unwind_spot_fn=lambda: exchange.buy_spot(position.symbol, position.size),
            unwind_perp_fn=lambda: exchange.open_short(
                position.symbol, position.size, position.leverage
            ),
        )

        if spot_result is None or perp_result is None:
            return ExitResult(
                success=False, spot_pnl=0,
                funding_earned=position.total_funding_earned, total_pnl=0,
                reason="Atomic execution returned None",
            )

        if not spot_result.success or not perp_result.success:
            return ExitResult(
                success=False, spot_pnl=0,
                funding_earned=position.total_funding_earned, total_pnl=0,
                reason="Exit partially failed — positions may be in inconsistent state",
            )

        # Calculate PnL
        spot_pnl = (spot_result.price - position.spot_entry_price) * position.size
        perp_pnl = (position.futures_entry_price - perp_result.price) * position.size
        total_pnl = spot_pnl + perp_pnl + position.total_funding_earned

        # Remove from tracked positions
        self._positions = [
            p for p in self._positions
            if not (p.symbol == position.symbol and p.exchange == position.exchange)
        ]

        result = ExitResult(
            success=True,
            spot_pnl=spot_pnl + perp_pnl,
            funding_earned=position.total_funding_earned,
            total_pnl=total_pnl,
            reason="Clean exit",
        )
        logger.info("Exited: %s", result)
        return result

    # ------------------------------------------------------------------
    # Atomic execution
    # ------------------------------------------------------------------
    async def _execute_atomic(
        self,
        spot_fn: Callable[[], Coroutine[Any, Any, Any]],
        perp_fn: Callable[[], Coroutine[Any, Any, Any]],
        unwind_spot_fn: Callable[[], Coroutine[Any, Any, Any]],
        unwind_perp_fn: Callable[[], Coroutine[Any, Any, Any]],
    ) -> tuple[Any, Any]:
        """
        Execute two orders as simultaneously as possible.
        If one fails, immediately reverse the other.
        """
        try:
            results = await asyncio.gather(
                spot_fn(), perp_fn(), return_exceptions=True,
            )
        except Exception as exc:
            logger.error("Gather failed: %s", exc)
            return None, None

        spot_result, perp_result = results

        # Handle exceptions from gather
        if isinstance(spot_result, Exception):
            logger.error("Spot order exception: %s", spot_result)
            spot_result = _SimulatedOrder(success=False, error=str(spot_result))
        if isinstance(perp_result, Exception):
            logger.error("Perp order exception: %s", perp_result)
            perp_result = _SimulatedOrder(success=False, error=str(perp_result))

        spot_ok = getattr(spot_result, "success", False)
        perp_ok = getattr(perp_result, "success", False)

        # Both succeeded
        if spot_ok and perp_ok:
            return spot_result, perp_result

        # One leg failed — unwind the other
        if spot_ok and not perp_ok:
            logger.error("Perp leg failed — unwinding spot")
            try:
                await unwind_spot_fn()
                logger.info("Spot unwind successful")
            except Exception as exc:
                logger.critical("SPOT UNWIND FAILED: %s — MANUAL INTERVENTION NEEDED", exc)

        if perp_ok and not spot_ok:
            logger.error("Spot leg failed — unwinding perp")
            try:
                await unwind_perp_fn()
                logger.info("Perp unwind successful")
            except Exception as exc:
                logger.critical("PERP UNWIND FAILED: %s — MANUAL INTERVENTION NEEDED", exc)

        return spot_result, perp_result

    # ------------------------------------------------------------------
    # Dry-run helpers
    # ------------------------------------------------------------------
    async def _enter_dry_run(
        self, decision: EntryDecision, exchange: "BaseExchange"
    ) -> ArbPosition:
        """Simulate an entry in dry-run mode."""
        try:
            spot_price = await exchange.get_spot_price(decision.symbol)
            futures_price = await exchange.get_futures_price(decision.symbol)
        except Exception:
            spot_price = 100.0  # fallback for mock
            futures_price = 100.05

        position = ArbPosition(
            symbol=decision.symbol,
            exchange=decision.exchange,
            spot_entry_price=spot_price,
            futures_entry_price=futures_price,
            size=decision.size,
            entry_time=time.time(),
            leverage=min(self.leverage, _ABSOLUTE_MAX_LEVERAGE),
        )
        self._positions.append(position)
        logger.info("[DRY RUN] Simulated entry: %s", position)
        return position

    async def _exit_dry_run(
        self, position: ArbPosition, exchange: "BaseExchange"
    ) -> ExitResult:
        """Simulate an exit in dry-run mode."""
        try:
            spot_price = await exchange.get_spot_price(position.symbol)
            futures_price = await exchange.get_futures_price(position.symbol)
        except Exception:
            spot_price = position.spot_entry_price * 1.001
            futures_price = position.futures_entry_price * 1.001

        spot_pnl = (spot_price - position.spot_entry_price) * position.size
        perp_pnl = (position.futures_entry_price - futures_price) * position.size
        total_pnl = spot_pnl + perp_pnl + position.total_funding_earned

        self._positions = [
            p for p in self._positions
            if not (p.symbol == position.symbol and p.exchange == position.exchange)
        ]

        result = ExitResult(
            success=True,
            spot_pnl=spot_pnl + perp_pnl,
            funding_earned=position.total_funding_earned,
            total_pnl=total_pnl,
            reason="[DRY RUN] Simulated exit",
        )
        logger.info("[DRY RUN] Simulated exit: %s", result)
        return result
