"""Unified configuration loader for all bots."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def get(key: str, default: str | None = None, required: bool = False) -> str:
    """Get environment variable with optional enforcement."""
    value = os.getenv(key, default)
    if required and not value:
        raise ValueError(f"Missing required env var: {key}")
    return value


# --- System ---
DRY_RUN = get("DRY_RUN", "true").lower() == "true"
LOG_LEVEL = get("LOG_LEVEL", "INFO")
DB_PATH = get("DB_PATH", "./data/trading.db")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = get("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALERT_CHAT_ID = get("TELEGRAM_ALERT_CHAT_ID")

# --- Polymarket ---
POLY_PRIVATE_KEY = get("POLY_PRIVATE_KEY")
POLY_API_KEY = get("POLY_API_KEY")
POLY_API_SECRET = get("POLY_API_SECRET")
POLY_PASSPHRASE = get("POLY_PASSPHRASE")
FUNDER_ADDRESS = get("FUNDER_ADDRESS")
SIGNATURE_TYPE = int(get("SIGNATURE_TYPE", "2"))

# --- Binance ---
BINANCE_API_KEY = get("BINANCE_API_KEY")
BINANCE_API_SECRET = get("BINANCE_API_SECRET")

# --- Bybit ---
BYBIT_API_KEY = get("BYBIT_API_KEY")
BYBIT_API_SECRET = get("BYBIT_API_SECRET")

# --- Hyperliquid ---
HYPERLIQUID_API_KEY = get("HYPERLIQUID_API_KEY")
HYPERLIQUID_API_SECRET = get("HYPERLIQUID_API_SECRET")

# --- dYdX ---
DYDX_API_KEY = get("DYDX_API_KEY")
DYDX_API_SECRET = get("DYDX_API_SECRET")
DYDX_PASSPHRASE = get("DYDX_PASSPHRASE")
