"""Performance metrics tracker for the market maker bot."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root so shared/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from shared import config
from shared.logger import get_logger

logger = get_logger("polymarket-maker", config.LOG_LEVEL)


# ------------------------------------------------------------------
# Data types
# ------------------------------------------------------------------

@dataclass
class Fill:
    """A single recorded fill."""
    order_id: str
    price: float
    size: float
    side: str  # "BUY" or "SELL"
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ------------------------------------------------------------------
# Tracker
# ------------------------------------------------------------------

class MetricsTracker:
    """Collects fills and cancels, computes performance summaries."""

    def __init__(self) -> None:
        self._fills: list[Fill] = []
        self._cancels: list[dict] = []
        self._start_time: datetime = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_fill(self, order_id: str, price: float, size: float, side: str) -> None:
        """Record an order fill."""
        f = Fill(order_id=order_id, price=price, size=size, side=side)
        self._fills.append(f)
        logger.info("Fill recorded", extra={
            "order_id": order_id,
            "price": price,
            "size": size,
            "side": side,
        })

    def record_cancel(self, order_id: str) -> None:
        """Record an order cancellation."""
        self._cancels.append({
            "order_id": order_id,
            "ts": datetime.now(timezone.utc),
        })

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def get_summary(self, period: str = "1h") -> dict:
        """
        Compute metrics for a given period.

        Args:
            period: "1h", "4h", "24h", or "all".

        Returns:
            Dict with total_trades, win_rate, total_pnl,
            spread_captured, position_turnover, cancel_count.
        """
        cutoff = self._period_cutoff(period)
        fills = [f for f in self._fills if f.ts >= cutoff]
        cancels = [c for c in self._cancels if c["ts"] >= cutoff]

        total_trades = len(fills)

        # Group fills into round-trips: buy then sell (simplified)
        buys = [f for f in fills if f.side == "BUY"]
        sells = [f for f in fills if f.side == "SELL"]

        total_buy_notional = sum(f.price * f.size for f in buys)
        total_sell_notional = sum(f.price * f.size for f in sells)
        total_pnl = total_sell_notional - total_buy_notional

        # Spread captured = average sell price - average buy price (when both exist)
        avg_buy = (total_buy_notional / sum(f.size for f in buys)) if buys else 0.0
        avg_sell = (total_sell_notional / sum(f.size for f in sells)) if sells else 0.0
        spread_captured = avg_sell - avg_buy if buys and sells else 0.0

        # Win rate: fraction of sells at higher price than average buy
        wins = sum(1 for f in sells if f.price > avg_buy) if buys else 0
        win_rate = (wins / len(sells) * 100.0) if sells else 0.0

        # Position turnover = total volume traded
        position_turnover = sum(f.price * f.size for f in fills)

        return {
            "period": period,
            "total_trades": total_trades,
            "buy_count": len(buys),
            "sell_count": len(sells),
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 4),
            "spread_captured": round(spread_captured, 6),
            "position_turnover": round(position_turnover, 2),
            "cancel_count": len(cancels),
        }

    def format_report(self, period: str = "1h") -> str:
        """Format metrics as a Telegram-friendly message."""
        s = self.get_summary(period)
        uptime = datetime.now(timezone.utc) - self._start_time
        hours = uptime.total_seconds() / 3600

        lines = [
            "<b>Market Maker Report</b>",
            f"Period: {s['period']} | Uptime: {hours:.1f}h",
            "",
            f"Trades: {s['total_trades']} (B:{s['buy_count']} / S:{s['sell_count']})",
            f"Win rate: {s['win_rate']:.1f}%",
            f"PnL: ${s['total_pnl']:.4f}",
            f"Spread captured: {s['spread_captured']:.6f}",
            f"Volume: ${s['position_turnover']:.2f}",
            f"Cancels: {s['cancel_count']}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _period_cutoff(period: str) -> datetime:
        """Convert period string to a UTC datetime cutoff."""
        now = datetime.now(timezone.utc)
        mapping = {
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "24h": timedelta(hours=24),
        }
        if period in mapping:
            return now - mapping[period]
        # "all" or unknown — return epoch
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
