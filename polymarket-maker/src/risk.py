"""Risk management and kill switch for the market maker bot."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

# Add project root so shared/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from shared import config
from shared.alerts import send_alert, send_kill_alert
from shared.logger import get_logger

if TYPE_CHECKING:
    from src.clob import PolymarketClient
    from src.strategy import Order

logger = get_logger("polymarket-maker", config.LOG_LEVEL)


# ------------------------------------------------------------------
# Data types
# ------------------------------------------------------------------

class HealthLevel(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


@dataclass
class HealthStatus:
    """Result of a periodic health check."""
    level: HealthLevel
    message: str
    action: str  # "none", "reduce", "kill"

    def __str__(self) -> str:
        return f"[{self.level.value}] {self.message} -> {self.action}"


@dataclass
class RiskConfig:
    """All risk thresholds in one place."""
    max_daily_loss_pct: float = 5.0
    max_position_per_market: float = 500.0
    max_total_exposure: float = 5000.0
    max_open_orders: int = 50

    @classmethod
    def from_dict(cls, data: dict) -> RiskConfig:
        return cls(
            max_daily_loss_pct=data.get("max_daily_loss_pct", 5.0),
            max_position_per_market=data.get("max_position_per_market", 500.0),
            max_total_exposure=data.get("max_total_exposure", 5000.0),
            max_open_orders=data.get("max_open_orders", 50),
        )


# ------------------------------------------------------------------
# Risk manager
# ------------------------------------------------------------------

class RiskManager:
    """Pre-trade and periodic risk checks with emergency kill switch."""

    def __init__(self, cfg: RiskConfig | dict | None = None) -> None:
        if cfg is None:
            self.cfg = RiskConfig()
        elif isinstance(cfg, dict):
            self.cfg = RiskConfig.from_dict(cfg)
        else:
            self.cfg = cfg

        # Running PnL state
        self._daily_pnl: float = 0.0
        self._day_start: datetime = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        self._fills: list[dict] = []
        self._killed = False

        logger.info("RiskManager initialised", extra={
            "max_daily_loss_pct": self.cfg.max_daily_loss_pct,
            "max_position_per_market": self.cfg.max_position_per_market,
            "max_total_exposure": self.cfg.max_total_exposure,
            "max_open_orders": self.cfg.max_open_orders,
        })

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def check_order(
        self,
        order: Order,
        inventory: dict[str, float],
        current_pnl: float,
        open_order_count: int = 0,
    ) -> tuple[bool, str]:
        """
        Pre-trade risk gate.

        Args:
            order: The Order to validate.
            inventory: {token_id: net_position_usd} map.
            current_pnl: Realised PnL so far today.
            open_order_count: Number of currently open orders.

        Returns:
            (allowed, reason) — if allowed is False, reason explains why.
        """
        if self._killed:
            return False, "Kill switch is active — no new orders"

        # 1. Daily loss limit
        loss_limit = self.cfg.max_total_exposure * (self.cfg.max_daily_loss_pct / 100.0)
        if current_pnl < -loss_limit:
            return False, f"Daily loss limit breached: PnL {current_pnl:.2f} < -{loss_limit:.2f}"

        # 2. Per-market position limit
        market_pos = abs(inventory.get(order.token_id, 0.0))
        potential = market_pos + order.size
        if potential > self.cfg.max_position_per_market:
            return False, (
                f"Position limit: current {market_pos:.2f} + order {order.size:.2f} "
                f"= {potential:.2f} > {self.cfg.max_position_per_market:.2f}"
            )

        # 3. Total exposure limit
        total_exposure = sum(abs(v) for v in inventory.values())
        if total_exposure + order.size > self.cfg.max_total_exposure:
            return False, (
                f"Total exposure limit: {total_exposure:.2f} + {order.size:.2f} "
                f"> {self.cfg.max_total_exposure:.2f}"
            )

        # 4. Max open orders
        if open_order_count >= self.cfg.max_open_orders:
            return False, f"Max open orders reached: {open_order_count} >= {self.cfg.max_open_orders}"

        return True, "OK"

    # ------------------------------------------------------------------
    # Periodic health check
    # ------------------------------------------------------------------

    def check_health(self, pnl: float, positions: dict[str, float]) -> HealthStatus:
        """
        Periodic health assessment.

        Args:
            pnl: Realised PnL since midnight UTC.
            positions: {token_id: net_position_usd} map.

        Returns:
            HealthStatus with level and recommended action.
        """
        loss_limit = self.cfg.max_total_exposure * (self.cfg.max_daily_loss_pct / 100.0)
        total_exposure = sum(abs(v) for v in positions.values())

        # RED — kill switch territory
        if pnl < -loss_limit:
            return HealthStatus(
                level=HealthLevel.RED,
                message=f"Daily loss {pnl:.2f} exceeds limit {-loss_limit:.2f}",
                action="kill",
            )

        if total_exposure > self.cfg.max_total_exposure:
            return HealthStatus(
                level=HealthLevel.RED,
                message=f"Total exposure {total_exposure:.2f} exceeds {self.cfg.max_total_exposure:.2f}",
                action="kill",
            )

        # YELLOW — approaching limits (within 80 %)
        warnings: list[str] = []
        if pnl < -loss_limit * 0.8:
            warnings.append(f"PnL {pnl:.2f} approaching loss limit")
        if total_exposure > self.cfg.max_total_exposure * 0.8:
            warnings.append(f"Exposure {total_exposure:.2f} approaching limit")

        for token_id, pos in positions.items():
            if abs(pos) > self.cfg.max_position_per_market * 0.8:
                warnings.append(f"Position in {token_id[:8]}… near limit ({abs(pos):.2f})")

        if warnings:
            return HealthStatus(
                level=HealthLevel.YELLOW,
                message="; ".join(warnings),
                action="reduce",
            )

        # GREEN
        return HealthStatus(
            level=HealthLevel.GREEN,
            message=f"PnL={pnl:.2f}, exposure={total_exposure:.2f}",
            action="none",
        )

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    async def kill_switch(self, client: PolymarketClient, reason: str) -> None:
        """
        EMERGENCY: cancel all orders, send Telegram alert, log event.

        This is the last line of defence. Once triggered the manager
        blocks all new orders until manually reset.
        """
        self._killed = True
        logger.critical("KILL SWITCH ACTIVATED", extra={"reason": reason})

        # Cancel all orders
        try:
            count = client.cancel_all_orders()
            logger.info("Kill switch cancelled orders", extra={"count": count})
        except Exception:
            logger.exception("Kill switch failed to cancel orders")

        # Send Telegram alert
        try:
            await send_kill_alert("polymarket-maker", reason)
        except Exception:
            logger.exception("Kill switch failed to send Telegram alert")

    def reset_kill_switch(self) -> None:
        """Manual reset after investigation."""
        self._killed = False
        logger.warning("Kill switch RESET — trading will resume")

    @property
    def is_killed(self) -> bool:
        return self._killed

    # ------------------------------------------------------------------
    # PnL tracking
    # ------------------------------------------------------------------

    def record_fill(self, price: float, size: float, side: str) -> None:
        """Record a fill for daily PnL tracking."""
        # Buys are negative cash flow, sells are positive
        cash_flow = price * size if side == "SELL" else -(price * size)
        self._fills.append({
            "ts": datetime.now(timezone.utc),
            "price": price,
            "size": size,
            "side": side,
            "cash_flow": cash_flow,
        })
        self._daily_pnl += cash_flow

    def get_daily_pnl(self) -> float:
        """Calculate PnL since midnight UTC."""
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Reset if day has rolled over
        if midnight > self._day_start:
            self._day_start = midnight
            self._daily_pnl = 0.0
            self._fills = [f for f in self._fills if f["ts"] >= midnight]
            self._daily_pnl = sum(f["cash_flow"] for f in self._fills)

        return self._daily_pnl
