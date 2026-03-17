"""
Unified Telegram monitor -- aggregates status from all 3 bots.
Reads health.json from each bot's data volume.
Responds to /status and /killall commands via python-telegram-bot.
Sends periodic hourly reports.
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

# Base data directory (volumes mounted here by docker-compose)
DATA_BASE = Path("/app/data")

BOT_NAMES = ["solana-bot", "poly-maker", "funding-arb"]

REPORT_INTERVAL_MINUTES = 60


# ---------------------------------------------------------------------------
# Health reading
# ---------------------------------------------------------------------------

def read_bot_health(bot_name: str) -> dict:
    """Read a bot's health.json from its mounted data volume."""
    health_file = DATA_BASE / bot_name / "health.json"
    try:
        if health_file.exists():
            data = json.loads(health_file.read_text())
            data.setdefault("bot", bot_name)
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Failed to read health for {bot_name}: {exc}")
    return {"bot": bot_name, "status": "offline", "pnl": 0.0, "errors": 0, "timestamp": None}


def read_all_health() -> dict[str, dict]:
    """Read health for every bot."""
    return {name: read_bot_health(name) for name in BOT_NAMES}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_status_report() -> str:
    """Format aggregated status report for Telegram."""
    healths = read_all_health()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_pnl = sum(h.get("pnl", 0.0) for h in healths.values())

    status_emoji = {"running": "🟢", "stopped": "🔴", "error": "🟡", "offline": "⚪", "unknown": "⚪"}
    mode = "🧪 DRY RUN MODE" if DRY_RUN else "🔴 LIVE MODE"

    lines = [
        "<b>📊 System Status Report</b>",
        f"<i>{now}</i>",
        mode,
        "",
    ]

    for bot_name, health in healths.items():
        status = health.get("status", "offline")
        emoji = status_emoji.get(status, "⚪")
        pnl = health.get("pnl", 0.0)
        errors = health.get("errors", 0)
        ts = health.get("timestamp") or health.get("last_heartbeat") or "never"
        lines.append(f"{emoji} <b>{bot_name}</b>")
        lines.append(f"   PnL: ${pnl:.2f} | Errors: {errors}")
        lines.append(f"   Last heartbeat: {ts}")
        lines.append("")

    lines.append(f"<b>Total PnL: ${total_pnl:.2f}</b>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Kill signal
# ---------------------------------------------------------------------------

def write_kill_signal():
    """Write KILL_SIGNAL to every bot's data directory."""
    ts = datetime.now(timezone.utc).isoformat()
    for bot_name in BOT_NAMES:
        kill_file = DATA_BASE / bot_name / "KILL_SIGNAL"
        kill_file.parent.mkdir(parents=True, exist_ok=True)
        kill_file.write_text(ts)
    logger.warning("Kill signal written to all bot data dirs")


def check_kill_signal() -> bool:
    """Check if any kill signal is active."""
    return any((DATA_BASE / name / "KILL_SIGNAL").exists() for name in BOT_NAMES)


def clear_kill_signal():
    """Clear kill signal from all bots."""
    for name in BOT_NAMES:
        f = DATA_BASE / name / "KILL_SIGNAL"
        if f.exists():
            f.unlink()
    logger.info("Kill signals cleared")


# ---------------------------------------------------------------------------
# Telegram bot command handlers (python-telegram-bot)
# ---------------------------------------------------------------------------

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    HAS_PTB = True
except ImportError:
    HAS_PTB = False
    logger.warning("python-telegram-bot not installed; falling back to polling-only mode")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    if str(update.effective_chat.id) != str(TELEGRAM_ALERT_CHAT_ID):
        return
    report = format_status_report()
    await update.message.reply_text(report, parse_mode="HTML")
    logger.info("/status command handled")


async def cmd_killall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /killall command -- emergency stop all bots."""
    if str(update.effective_chat.id) != str(TELEGRAM_ALERT_CHAT_ID):
        return
    logger.warning("/killall triggered by Telegram command")
    write_kill_signal()
    await update.message.reply_text(
        "🚨 <b>KILLALL ACTIVATED</b>\n"
        "Kill signal written to all bot data directories.\n"
        "All bots should stop trading within 60 seconds.",
        parse_mode="HTML",
    )
    await send_alert(
        "🚨 <b>KILLALL ACTIVATED</b>\nSending stop signal to all bots.\nAll orders will be cancelled."
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /resume command -- clear kill signals."""
    if str(update.effective_chat.id) != str(TELEGRAM_ALERT_CHAT_ID):
        return
    clear_kill_signal()
    await update.message.reply_text("✅ Kill signals cleared. Bots may resume trading.", parse_mode="HTML")
    logger.info("/resume command handled")


# ---------------------------------------------------------------------------
# Periodic report job
# ---------------------------------------------------------------------------

async def periodic_report(context: ContextTypes.DEFAULT_TYPE):
    """Send hourly status report and warn if kill signal is active."""
    if check_kill_signal():
        await send_alert("⚠️ Kill signal is active. All bots should be stopped.")
    report = format_status_report()
    await send_alert(report)
    logger.info("Periodic status report sent")


# ---------------------------------------------------------------------------
# Fallback loop (no python-telegram-bot)
# ---------------------------------------------------------------------------

async def fallback_loop():
    """Simple loop that sends status via httpx every hour (no command handling)."""
    logger.info(f"Fallback monitor loop — reporting every {REPORT_INTERVAL_MINUTES}m")
    while True:
        if check_kill_signal():
            await send_alert("⚠️ Kill signal is active. All bots should be stopped.")
        report = format_status_report()
        await send_alert(report)
        logger.info("Status report sent")
        await asyncio.sleep(REPORT_INTERVAL_MINUTES * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    """Entry point."""
    logger.info("Telegram monitor starting...")
    await send_alert("🚀 <b>Monitor Online</b>\nWatching all 3 trading bots.")

    if HAS_PTB and TELEGRAM_BOT_TOKEN:
        logger.info("Starting with python-telegram-bot (command handling enabled)")
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Register command handlers
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("killall", cmd_killall))
        app.add_handler(CommandHandler("resume", cmd_resume))

        # Schedule periodic report every hour
        app.job_queue.run_repeating(
            periodic_report,
            interval=REPORT_INTERVAL_MINUTES * 60,
            first=REPORT_INTERVAL_MINUTES * 60,
        )

        # Run the bot (blocks until stopped)
        async with app:
            await app.start()
            await app.updater.start_polling()
            logger.info("Monitor bot is polling for commands...")
            # Keep running until interrupted
            stop_event = asyncio.Event()
            try:
                await stop_event.wait()
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                await app.updater.stop()
                await app.stop()
    else:
        logger.info("Falling back to simple polling loop (no command handling)")
        try:
            await fallback_loop()
        except KeyboardInterrupt:
            pass

    logger.info("Monitor shutting down")
    await send_alert("⏹️ Monitor going offline.")


if __name__ == "__main__":
    asyncio.run(main())
