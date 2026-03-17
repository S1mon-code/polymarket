"""
Unified Telegram monitor — aggregates status from all 3 bots.
Sends periodic reports and handles /killall emergency stop.
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID, DRY_RUN
from shared.logger import get_logger
from shared.alerts import send_alert

logger = get_logger("monitor")

# Bot status tracking
bot_status: dict[str, dict] = {
    "solana-bot": {"status": "unknown", "last_heartbeat": None, "pnl": 0.0, "errors": 0},
    "poly-maker": {"status": "unknown", "last_heartbeat": None, "pnl": 0.0, "errors": 0},
    "funding-arb": {"status": "unknown", "last_heartbeat": None, "pnl": 0.0, "errors": 0},
}


def format_status_report() -> str:
    """Format aggregated status report for Telegram."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_pnl = sum(b["pnl"] for b in bot_status.values())

    lines = [
        f"<b>📊 System Status Report</b>",
        f"<i>{now}</i>",
        f"{'🧪 DRY RUN MODE' if DRY_RUN else '🔴 LIVE MODE'}",
        "",
    ]

    status_emoji = {"running": "🟢", "stopped": "🔴", "error": "🟡", "unknown": "⚪"}

    for bot_name, status in bot_status.items():
        emoji = status_emoji.get(status["status"], "⚪")
        heartbeat = status["last_heartbeat"] or "never"
        lines.append(f"{emoji} <b>{bot_name}</b>")
        lines.append(f"   PnL: ${status['pnl']:.2f} | Errors: {status['errors']}")
        lines.append(f"   Last heartbeat: {heartbeat}")
        lines.append("")

    lines.append(f"<b>Total PnL: ${total_pnl:.2f}</b>")
    return "\n".join(lines)


async def send_status_report():
    """Send periodic status report."""
    report = format_status_report()
    await send_alert(report)
    logger.info("Status report sent")


async def handle_killall():
    """Emergency: stop all bots."""
    logger.warning("KILLALL triggered — stopping all bots")
    await send_alert(
        "🚨 <b>KILLALL ACTIVATED</b>\n"
        "Sending stop signal to all bots.\n"
        "All orders will be cancelled."
    )
    # Write kill signal file that each bot checks
    kill_file = Path(__file__).parent.parent / "data" / "KILL_SIGNAL"
    kill_file.parent.mkdir(parents=True, exist_ok=True)
    kill_file.write_text(datetime.now(timezone.utc).isoformat())
    logger.warning("Kill signal written")


def check_kill_signal() -> bool:
    """Check if kill signal is active."""
    kill_file = Path(__file__).parent.parent / "data" / "KILL_SIGNAL"
    return kill_file.exists()


def clear_kill_signal():
    """Clear kill signal after manual review."""
    kill_file = Path(__file__).parent.parent / "data" / "KILL_SIGNAL"
    if kill_file.exists():
        kill_file.unlink()


def update_bot_status(bot_name: str, status: str, pnl: float = 0.0, errors: int = 0):
    """Update status for a specific bot (called by each bot's heartbeat)."""
    if bot_name in bot_status:
        bot_status[bot_name].update({
            "status": status,
            "last_heartbeat": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "pnl": pnl,
            "errors": errors,
        })


async def monitor_loop(interval_minutes: int = 60):
    """Main monitoring loop."""
    logger.info(f"Monitor started — reporting every {interval_minutes}m")

    while True:
        if check_kill_signal():
            await send_alert("⚠️ Kill signal is active. All bots should be stopped.")

        await send_status_report()
        await asyncio.sleep(interval_minutes * 60)


async def main():
    """Entry point."""
    logger.info("Telegram monitor starting...")
    await send_alert("🚀 <b>Monitor Online</b>\nWatching all 3 trading bots.")

    try:
        await monitor_loop(interval_minutes=60)
    except KeyboardInterrupt:
        logger.info("Monitor shutting down")
        await send_alert("⏹️ Monitor going offline.")


if __name__ == "__main__":
    asyncio.run(main())
