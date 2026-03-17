"""Polymarket CLOB API wrapper using py-clob-client."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root so shared/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType

from shared import config
from shared.logger import get_logger

logger = get_logger("polymarket-maker", config.LOG_LEVEL)

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon


class PolymarketClient:
    """Thin wrapper around ClobClient with logging and DRY_RUN support."""

    def __init__(self) -> None:
        """Initialize ClobClient with credentials from shared config."""
        creds = ApiCreds(
            api_key=config.POLY_API_KEY,
            api_secret=config.POLY_API_SECRET,
            api_passphrase=config.POLY_PASSPHRASE,
        )
        self._client = ClobClient(
            host=CLOB_HOST,
            chain_id=CHAIN_ID,
            key=config.POLY_PRIVATE_KEY,
            creds=creds,
            signature_type=config.SIGNATURE_TYPE,
            funder=config.FUNDER_ADDRESS,
        )
        self._dry_run = config.DRY_RUN
        logger.info("ClobClient initialised", extra={
            "host": CLOB_HOST,
            "chain_id": CHAIN_ID,
            "dry_run": self._dry_run,
        })

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_markets(self, next_cursor: str = "") -> list[dict]:
        """Fetch active markets from CLOB API."""
        try:
            resp = self._client.get_markets(next_cursor=next_cursor)
            markets: list[dict] = resp.get("data", []) if isinstance(resp, dict) else []
            logger.info("Fetched markets", extra={"count": len(markets)})
            return markets
        except Exception:
            logger.exception("Failed to fetch markets")
            return []

    def get_market(self, condition_id: str) -> dict:
        """Get specific market details."""
        try:
            market = self._client.get_market(condition_id=condition_id)
            return market if isinstance(market, dict) else {}
        except Exception:
            logger.exception("Failed to fetch market", extra={"condition_id": condition_id})
            return {}

    def get_orderbook(self, token_id: str) -> dict:
        """Get orderbook for a token."""
        try:
            book = self._client.get_order_book(token_id=token_id)
            return book if isinstance(book, dict) else {}
        except Exception:
            logger.exception("Failed to fetch orderbook", extra={"token_id": token_id})
            return {}

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def place_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
    ) -> dict:
        """
        Place a GTC limit order.

        Args:
            token_id: The token to trade.
            price: Limit price (0.01 – 0.99).
            size: Order size in USDC.
            side: "BUY" or "SELL".

        Returns:
            Order confirmation dict with order_id, or empty dict on failure.
        """
        if not 0.01 <= price <= 0.99:
            logger.error("Price out of range", extra={"price": price})
            return {}

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            order_type=OrderType.GTC,
        )

        if self._dry_run:
            logger.info("[DRY_RUN] Would place order", extra={
                "token_id": token_id,
                "price": price,
                "size": size,
                "side": side,
            })
            return {"order_id": "dry_run", "status": "simulated"}

        try:
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, order_type=OrderType.GTC)
            logger.info("Order placed", extra={
                "order_id": resp.get("orderID"),
                "token_id": token_id,
                "price": price,
                "size": size,
                "side": side,
            })
            return resp if isinstance(resp, dict) else {}
        except Exception:
            logger.exception("Failed to place order", extra={
                "token_id": token_id,
                "price": price,
                "size": size,
                "side": side,
            })
            return {}

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order. Returns True on success."""
        if self._dry_run:
            logger.info("[DRY_RUN] Would cancel order", extra={"order_id": order_id})
            return True

        try:
            self._client.cancel(order_id=order_id)
            logger.info("Order cancelled", extra={"order_id": order_id})
            return True
        except Exception:
            logger.exception("Failed to cancel order", extra={"order_id": order_id})
            return False

    def cancel_all_orders(self) -> int:
        """Cancel ALL open orders. Returns count cancelled."""
        if self._dry_run:
            logger.info("[DRY_RUN] Would cancel all orders")
            return 0

        try:
            resp = self._client.cancel_all()
            cancelled = resp.get("canceled", []) if isinstance(resp, dict) else []
            count = len(cancelled) if isinstance(cancelled, list) else 0
            logger.info("All orders cancelled", extra={"count": count})
            return count
        except Exception:
            logger.exception("Failed to cancel all orders")
            return 0

    # ------------------------------------------------------------------
    # Account queries
    # ------------------------------------------------------------------

    def get_open_orders(self, market: str | None = None) -> list[dict]:
        """Get all open orders, optionally filtered by market."""
        try:
            if market:
                resp = self._client.get_orders(
                    params={"market": market, "state": "OPEN"},
                )
            else:
                resp = self._client.get_orders(params={"state": "OPEN"})
            orders: list[dict] = resp if isinstance(resp, list) else []
            return orders
        except Exception:
            logger.exception("Failed to fetch open orders")
            return []

    def get_positions(self) -> list[dict]:
        """Get current positions (balances of YES/NO tokens)."""
        try:
            resp = self._client.get_balances()
            positions: list[dict] = resp if isinstance(resp, list) else []
            return positions
        except Exception:
            logger.exception("Failed to fetch positions")
            return []

    def get_trades(self, market: str | None = None) -> list[dict]:
        """Get recent trade history."""
        try:
            params: dict = {}
            if market:
                params["market"] = market
            resp = self._client.get_trades(params=params)
            trades: list[dict] = resp if isinstance(resp, list) else []
            return trades
        except Exception:
            logger.exception("Failed to fetch trades")
            return []
