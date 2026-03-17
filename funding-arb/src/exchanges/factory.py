"""Exchange factory — create exchange instances from config + env vars."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Add shared utilities to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from shared import config as cfg  # noqa: E402

from src.exchanges.base import BaseExchange  # noqa: E402
from src.exchanges.binance import BinanceExchange  # noqa: E402
from src.exchanges.bybit import BybitExchange  # noqa: E402
from src.exchanges.dydx import DydxExchange  # noqa: E402
from src.exchanges.hyperliquid import HyperliquidExchange  # noqa: E402

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "exchanges.json"

_EXCHANGE_MAP: dict[str, type[BaseExchange]] = {
    "binance": BinanceExchange,
    "bybit": BybitExchange,
    "hyperliquid": HyperliquidExchange,
    "dydx": DydxExchange,
}

_KEY_MAP: dict[str, tuple[str, str, str | None]] = {
    "binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET", None),
    "bybit": ("BYBIT_API_KEY", "BYBIT_API_SECRET", None),
    "hyperliquid": ("HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET", None),
    "dydx": ("DYDX_API_KEY", "DYDX_API_SECRET", "DYDX_PASSPHRASE"),
}


def _load_config() -> dict:
    """Load exchange configuration from JSON."""
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load exchanges.json: %s — using defaults", exc)
        return {"exchanges": {}, "testnet": True}


def create_exchange(name: str, testnet: bool | None = None) -> BaseExchange:
    """Create a single exchange instance by name.

    API keys are read from environment variables via the shared config module.
    If *testnet* is None, it falls back to the config file setting.
    """
    name = name.lower()
    if name not in _EXCHANGE_MAP:
        raise ValueError(f"Unknown exchange: {name}. Choices: {list(_EXCHANGE_MAP)}")

    conf = _load_config()
    if testnet is None:
        testnet = conf.get("testnet", True)

    key_attr, secret_attr, pass_attr = _KEY_MAP[name]
    api_key = getattr(cfg, key_attr, "") or ""
    api_secret = getattr(cfg, secret_attr, "") or ""

    cls = _EXCHANGE_MAP[name]

    # dYdX needs a passphrase
    if name == "dydx":
        passphrase = getattr(cfg, pass_attr, "") or "" if pass_attr else None
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            passphrase=passphrase,
        )

    return cls(api_key=api_key, api_secret=api_secret, testnet=testnet)


def create_all_exchanges(testnet: bool | None = None) -> dict[str, BaseExchange]:
    """Create instances for every *enabled* exchange in the config."""
    conf = _load_config()
    if testnet is None:
        testnet = conf.get("testnet", True)

    exchanges: dict[str, BaseExchange] = {}
    for name, ex_conf in conf.get("exchanges", {}).items():
        if not ex_conf.get("enabled", False):
            logger.info("Skipping disabled exchange: %s", name)
            continue
        try:
            exchanges[name] = create_exchange(name, testnet=testnet)
            logger.info("Created exchange: %s (testnet=%s)", name, testnet)
        except Exception as exc:
            logger.error("Failed to create exchange %s: %s", name, exc)

    return exchanges
