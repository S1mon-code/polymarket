"""Position Health Monitor — tracks margin, basis, and funding rate health."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Coroutine, Any

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from src.exchanges.base import BaseExchange, FundingRate
except ImportError:
    BaseExchange = None  # type: ignore[assignment,misc]
    FundingRate = None  # type: ignore[assignment,misc]

from src.engine import ArbPosition

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class HealthAlert:
    level: str  # "info", "warning", "critical"
    message: str
    position: ArbPosition
    action_required: str

    def __repr__(self) -> str:
        return f"HealthAlert({self.level.upper()}: {self.message})"


@dataclass
class MarginStatus:
    exchange: str
    margin_ratio: float  # 0-1, lower is safer
    available_margin: float
    used_margin: float
    healthy: bool

    def __repr__(self) -> str:
        status = "HEALTHY" if self.healthy else "UNHEALTHY"
        return f"Margin({self.exchange}: {self.margin_ratio:.1%} — {status})"


# Thresholds
_MARGIN_WARN = 0.50
_MARGIN_EMERGENCY = 0.70
_BASIS_WARN = 0.02  # 2%
_BASIS_CRITICAL = 0.05  # 5%


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------
class PositionMonitor:
    """Monitors health of all active arb positions."""

    def __init__(
        self,
        exchanges: dict[str, "BaseExchange"],
        alert_fn: Callable[[str], Coroutine[Any, Any, bool]] | None = None,
    ) -> None:
        self.exchanges = exchanges
        self.alert_fn = alert_fn  # e.g., shared.alerts.send_alert

    # ------------------------------------------------------------------
    # Core health checks
    # ------------------------------------------------------------------
    async def check_all_positions(
        self, positions: list[ArbPosition]
    ) -> list[HealthAlert]:
        """Run all health checks on every position."""
        alerts: list[HealthAlert] = []

        for position in positions:
            pos_alerts = await self._check_position(position)
            alerts.extend(pos_alerts)

        # Send critical alerts immediately
        for alert in alerts:
            if alert.level in ("warning", "critical") and self.alert_fn:
                try:
                    await self.alert_fn(
                        f"[{alert.level.upper()}] {alert.message}\n"
                        f"Action: {alert.action_required}"
                    )
                except Exception as exc:
                    logger.error("Failed to send alert: %s", exc)

        return alerts

    async def check_margin(self, exchange_name: str) -> MarginStatus:
        """Check margin health on a specific exchange."""
        exchange = self.exchanges.get(exchange_name)
        if exchange is None:
            return MarginStatus(
                exchange=exchange_name,
                margin_ratio=1.0,
                available_margin=0.0,
                used_margin=0.0,
                healthy=False,
            )

        try:
            margin_ratio = await exchange.get_margin_ratio()
            balance = await exchange.get_balance()
            total = sum(balance.values())
            used = total * margin_ratio
            available = total - used
        except Exception as exc:
            logger.error("Margin check failed for %s: %s", exchange_name, exc)
            return MarginStatus(
                exchange=exchange_name,
                margin_ratio=1.0,
                available_margin=0.0,
                used_margin=0.0,
                healthy=False,
            )

        return MarginStatus(
            exchange=exchange_name,
            margin_ratio=margin_ratio,
            available_margin=available,
            used_margin=used,
            healthy=margin_ratio < _MARGIN_WARN,
        )

    # ------------------------------------------------------------------
    # Status reporting
    # ------------------------------------------------------------------
    def format_status_report(self, positions: list[ArbPosition]) -> str:
        """Format all positions as a Telegram-friendly status report."""
        if not positions:
            return "<b>Funding Arb Bot</b>\n\nNo active positions."

        lines = ["<b>Funding Arb Bot — Status</b>\n"]

        total_funding = 0.0
        total_pnl = 0.0
        total_notional = 0.0

        for i, pos in enumerate(positions, 1):
            total_funding += pos.total_funding_earned
            total_pnl += pos.realized_pnl + pos.total_funding_earned
            total_notional += pos.notional_usd

            age_str = f"{pos.age_hours:.1f}h"
            lines.append(
                f"{i}. <b>{pos.symbol}</b> @ {pos.exchange}\n"
                f"   Size: {pos.size:.6f} (${pos.notional_usd:.2f})\n"
                f"   Funding: ${pos.total_funding_earned:.4f}\n"
                f"   Age: {age_str}"
            )

        lines.append(f"\n<b>Summary</b>")
        lines.append(f"Positions: {len(positions)}")
        lines.append(f"Total Notional: ${total_notional:.2f}")
        lines.append(f"Total Funding: ${total_funding:.4f}")
        lines.append(f"Total PnL: ${total_pnl:.4f}")
        lines.append(f"Time: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _check_position(self, position: ArbPosition) -> list[HealthAlert]:
        """Run all health checks on a single position."""
        alerts: list[HealthAlert] = []
        exchange = self.exchanges.get(position.exchange)

        if exchange is None:
            alerts.append(
                HealthAlert(
                    level="critical",
                    message=f"Exchange {position.exchange} not available",
                    position=position,
                    action_required="Check exchange connectivity — may need manual exit",
                )
            )
            return alerts

        # 1. Margin ratio
        try:
            margin_ratio = await exchange.get_margin_ratio()
            if margin_ratio >= _MARGIN_EMERGENCY:
                alerts.append(
                    HealthAlert(
                        level="critical",
                        message=(
                            f"{position.symbol}@{position.exchange}: "
                            f"margin ratio {margin_ratio:.1%} — approaching liquidation"
                        ),
                        position=position,
                        action_required="EMERGENCY: reduce position or add margin immediately",
                    )
                )
            elif margin_ratio >= _MARGIN_WARN:
                alerts.append(
                    HealthAlert(
                        level="warning",
                        message=(
                            f"{position.symbol}@{position.exchange}: "
                            f"margin ratio {margin_ratio:.1%} — elevated"
                        ),
                        position=position,
                        action_required="Consider adding margin or reducing position size",
                    )
                )
        except Exception as exc:
            alerts.append(
                HealthAlert(
                    level="warning",
                    message=f"Could not check margin for {position.exchange}: {exc}",
                    position=position,
                    action_required="Check exchange API connectivity",
                )
            )

        # 2. Basis divergence
        try:
            spot_price = await exchange.get_spot_price(position.symbol)
            futures_price = await exchange.get_futures_price(position.symbol)
            if spot_price > 0:
                current_basis = (futures_price - spot_price) / spot_price
                if abs(current_basis) >= _BASIS_CRITICAL:
                    alerts.append(
                        HealthAlert(
                            level="critical",
                            message=(
                                f"{position.symbol}: basis={current_basis:.4%} "
                                f"— extreme divergence"
                            ),
                            position=position,
                            action_required="Consider exiting — basis risk too high",
                        )
                    )
                elif abs(current_basis) >= _BASIS_WARN:
                    alerts.append(
                        HealthAlert(
                            level="warning",
                            message=(
                                f"{position.symbol}: basis={current_basis:.4%} "
                                f"— elevated divergence"
                            ),
                            position=position,
                            action_required="Monitor closely — basis widening",
                        )
                    )
        except Exception as exc:
            alerts.append(
                HealthAlert(
                    level="warning",
                    message=f"Could not check basis for {position.symbol}: {exc}",
                    position=position,
                    action_required="Check price feed connectivity",
                )
            )

        # 3. Funding rate direction
        try:
            funding = await exchange.get_funding_rate(position.symbol)
            if funding.rate < 0:
                alerts.append(
                    HealthAlert(
                        level="warning",
                        message=(
                            f"{position.symbol}: funding rate turned negative "
                            f"({funding.rate:.4%})"
                        ),
                        position=position,
                        action_required="Consider exiting — paying funding instead of receiving",
                    )
                )
            elif funding.rate == 0:
                alerts.append(
                    HealthAlert(
                        level="info",
                        message=f"{position.symbol}: funding rate is zero",
                        position=position,
                        action_required="Monitor — rate may be turning negative",
                    )
                )
        except Exception as exc:
            alerts.append(
                HealthAlert(
                    level="warning",
                    message=f"Could not check funding rate for {position.symbol}: {exc}",
                    position=position,
                    action_required="Check exchange API connectivity",
                )
            )

        return alerts
