"""Real-time orderbook management via WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

# Polymarket CLOB WebSocket endpoint
DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class OrderBook:
    """Orderbook for a single token with bids and asks."""

    token_id: str
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, size) desc by price
    asks: list[tuple[float, float]] = field(default_factory=list)  # (price, size) asc by price
    timestamp: float = 0.0

    @property
    def best_bid(self) -> Optional[float]:
        """Highest bid price, or None if no bids."""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Lowest ask price, or None if no asks."""
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        """Mid-price between best bid and best ask."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return self.best_bid or self.best_ask

    @property
    def spread(self) -> Optional[float]:
        """Bid-ask spread. None if either side is empty."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    def weighted_mid_price(self, depth: int = 3) -> Optional[float]:
        """
        Calculate volume-weighted mid-price using top `depth` levels.

        Weights are proportional to the size at each level, giving
        more influence to levels with higher liquidity.
        """
        if not self.bids or not self.asks:
            return self.mid_price

        bid_levels = self.bids[:depth]
        ask_levels = self.asks[:depth]

        bid_value = sum(price * size for price, size in bid_levels)
        bid_size = sum(size for _, size in bid_levels)
        ask_value = sum(price * size for price, size in ask_levels)
        ask_size = sum(size for _, size in ask_levels)

        total_size = bid_size + ask_size
        if total_size == 0:
            return self.mid_price

        return (bid_value + ask_value) / total_size

    def update(self, data: dict) -> None:
        """
        Apply an incremental orderbook update.

        Expected data format:
        {
            "bids": [{"price": "0.50", "size": "100"}, ...],
            "asks": [{"price": "0.55", "size": "100"}, ...],
            "timestamp": 1234567890
        }

        A size of "0" means remove that price level.
        """
        if "timestamp" in data:
            self.timestamp = float(data["timestamp"])

        if "bids" in data:
            self._apply_updates(self.bids, data["bids"], reverse=True)

        if "asks" in data:
            self._apply_updates(self.asks, data["asks"], reverse=False)

    def set_snapshot(self, data: dict) -> None:
        """
        Set a full orderbook snapshot, replacing all existing data.

        Expected data format same as update().
        """
        if "timestamp" in data:
            self.timestamp = float(data["timestamp"])

        self.bids = []
        self.asks = []

        if "bids" in data:
            for level in data["bids"]:
                price = float(level["price"])
                size = float(level["size"])
                if size > 0:
                    self.bids.append((price, size))
            self.bids.sort(key=lambda x: x[0], reverse=True)

        if "asks" in data:
            for level in data["asks"]:
                price = float(level["price"])
                size = float(level["size"])
                if size > 0:
                    self.asks.append((price, size))
            self.asks.sort(key=lambda x: x[0])

    def _apply_updates(
        self,
        levels: list[tuple[float, float]],
        updates: list[dict],
        reverse: bool,
    ) -> None:
        """Apply incremental updates to a side of the book."""
        price_map: dict[float, float] = {price: size for price, size in levels}

        for update in updates:
            price = float(update["price"])
            size = float(update["size"])
            if size == 0:
                price_map.pop(price, None)
            else:
                price_map[price] = size

        levels.clear()
        levels.extend(sorted(price_map.items(), key=lambda x: x[0], reverse=reverse))


class OrderBookManager:
    """Manages real-time orderbook data for multiple tokens via WebSocket."""

    def __init__(self, ws_url: str = DEFAULT_WS_URL):
        self.ws_url: str = ws_url
        self.books: dict[str, OrderBook] = {}
        self._ws: Optional[ClientConnection] = None
        self._subscribed_tokens: set[str] = set()
        self._running: bool = False
        self._listen_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Connect to Polymarket WebSocket and start listening for updates."""
        logger.info("Connecting to orderbook WebSocket at %s", self.ws_url)
        self._ws = await websockets.connect(self.ws_url)
        self._running = True
        self._listen_task = asyncio.create_task(self._listen())
        logger.info("OrderBook WebSocket connected")

    async def disconnect(self) -> None:
        """Disconnect from WebSocket and clean up."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("OrderBook WebSocket disconnected")

    async def subscribe(self, token_ids: list[str]) -> None:
        """Subscribe to orderbook updates for specific tokens."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected. Call connect() first.")

        new_tokens = [t for t in token_ids if t not in self._subscribed_tokens]
        if not new_tokens:
            return

        for token_id in new_tokens:
            if token_id not in self.books:
                self.books[token_id] = OrderBook(token_id=token_id)

        subscribe_msg = {
            "type": "subscribe",
            "channel": "book",
            "assets_ids": new_tokens,
        }
        await self._ws.send(json.dumps(subscribe_msg))
        self._subscribed_tokens.update(new_tokens)
        logger.info("Subscribed to orderbook updates for %d tokens", len(new_tokens))

    async def unsubscribe(self, token_ids: list[str]) -> None:
        """Unsubscribe from orderbook updates for specific tokens."""
        if not self._ws:
            return

        tokens_to_remove = [t for t in token_ids if t in self._subscribed_tokens]
        if not tokens_to_remove:
            return

        unsubscribe_msg = {
            "type": "unsubscribe",
            "channel": "book",
            "assets_ids": tokens_to_remove,
        }
        await self._ws.send(json.dumps(unsubscribe_msg))
        self._subscribed_tokens -= set(tokens_to_remove)
        logger.info("Unsubscribed from %d tokens", len(tokens_to_remove))

    def get_book(self, token_id: str) -> OrderBook:
        """Get current orderbook snapshot for a token."""
        if token_id not in self.books:
            self.books[token_id] = OrderBook(token_id=token_id)
        return self.books[token_id]

    def get_mid_price(self, token_id: str) -> Optional[float]:
        """Get mid-price for a token."""
        return self.get_book(token_id).mid_price

    def get_spread(self, token_id: str) -> Optional[float]:
        """Get current bid-ask spread for a token."""
        return self.get_book(token_id).spread

    async def _listen(self) -> None:
        """Listen for WebSocket messages and update orderbooks."""
        while self._running and self._ws:
            try:
                raw = await self._ws.recv()
                message = json.loads(raw)
                self._handle_message(message)
            except websockets.ConnectionClosed:
                logger.warning("WebSocket connection closed, attempting reconnect...")
                await self._reconnect()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error processing WebSocket message")

    async def _reconnect(self) -> None:
        """Attempt to reconnect and resubscribe."""
        for attempt in range(5):
            try:
                wait_time = min(2 ** attempt, 30)
                logger.info("Reconnect attempt %d in %ds", attempt + 1, wait_time)
                await asyncio.sleep(wait_time)
                self._ws = await websockets.connect(self.ws_url)

                # Resubscribe to all previously subscribed tokens
                if self._subscribed_tokens:
                    tokens = list(self._subscribed_tokens)
                    self._subscribed_tokens.clear()
                    await self.subscribe(tokens)

                logger.info("Reconnected successfully")
                return
            except Exception:
                logger.exception("Reconnect attempt %d failed", attempt + 1)

        logger.error("Failed to reconnect after 5 attempts")
        self._running = False

    def _handle_message(self, message: dict) -> None:
        """Route an incoming WebSocket message to the appropriate handler."""
        msg_type = message.get("type", "")
        asset_id = message.get("asset_id", "")

        if not asset_id or asset_id not in self.books:
            return

        book = self.books[asset_id]

        if msg_type == "book_snapshot":
            book.set_snapshot(message)
            logger.debug("Snapshot received for %s", asset_id[:12])
        elif msg_type == "book_update":
            book.update(message)
        elif msg_type == "book_delta":
            book.update(message)
