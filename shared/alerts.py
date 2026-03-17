"""Telegram alert system shared by all bots."""

import asyncio
import httpx
from shared.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID


async def send_alert(message: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram alert message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALERT_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_ALERT_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception:
        return False


def send_alert_sync(message: str, parse_mode: str = "HTML") -> bool:
    """Synchronous wrapper for send_alert."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(send_alert(message, parse_mode))
    except RuntimeError:
        return asyncio.run(send_alert(message, parse_mode))


async def send_kill_alert(bot_name: str, reason: str) -> bool:
    """Send emergency kill switch alert."""
    msg = (
        f"🚨 <b>KILL SWITCH ACTIVATED</b>\n"
        f"Bot: {bot_name}\n"
        f"Reason: {reason}\n"
        f"All orders cancelled. Manual review required."
    )
    return await send_alert(msg)
