"""Hyperliquid exchange adapter — on-chain DEX, 1-hour funding intervals."""

from __future__ import annotations

import logging

import ccxt.async_support as ccxt

from src.exchanges.base import (
    BaseExchange,
    FundingRate,
    OrderResult,
    Position,
    _clamp_leverage,
)

logger = logging.getLogger(__name__)

FUNDING_INTERVAL_HOURS = 1  # Hourly funding — more opportunities


class HyperliquidExchange(BaseExchange):
    """Hyperliquid is perps-only; spot methods raise NotImplementedError."""

    name = "hyperliquid"

    def __init__(
        self, api_key: str, api_secret: str, testnet: bool = False
    ) -> None:
        super().__init__(api_key, api_secret, testnet)

        opts: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }

        self.exchange = ccxt.hyperliquid(opts)

        if testnet:
            self.exchange.set_sandbox_mode(True)

    # ------------------------------------------------------------------
    # Funding rates
    # ------------------------------------------------------------------
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        try:
            data = await self.exchange.fetch_funding_rate(symbol)
            rate = float(data.get("fundingRate", 0))
            next_time = float(data.get("fundingTimestamp", 0)) / 1000
            return FundingRate(
                symbol=symbol,
                exchange=self.name,
                rate=rate,
                next_funding_time=next_time,
                interval_hours=FUNDING_INTERVAL_HOURS,
                annualized_rate=rate * (8760 / FUNDING_INTERVAL_HOURS),
            )
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid get_funding_rate(%s) failed: %s", symbol, exc)
            raise

    async def get_all_funding_rates(self) -> list[FundingRate]:
        try:
            rates = await self.exchange.fetch_funding_rates()
            result: list[FundingRate] = []
            for sym, data in rates.items():
                rate = float(data.get("fundingRate", 0))
                next_time = float(data.get("fundingTimestamp", 0)) / 1000
                result.append(
                    FundingRate(
                        symbol=sym,
                        exchange=self.name,
                        rate=rate,
                        next_funding_time=next_time,
                        interval_hours=FUNDING_INTERVAL_HOURS,
                        annualized_rate=rate * (8760 / FUNDING_INTERVAL_HOURS),
                    )
                )
            return result
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid get_all_funding_rates failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------
    async def get_spot_price(self, symbol: str) -> float:
        """Hyperliquid is perps-only; fall back to futures price."""
        return await self.get_futures_price(symbol)

    async def get_futures_price(self, symbol: str) -> float:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid get_futures_price(%s) failed: %s", symbol, exc)
            raise

    # ------------------------------------------------------------------
    # Spot orders — not supported on Hyperliquid
    # ------------------------------------------------------------------
    async def buy_spot(self, symbol: str, size: float) -> OrderResult:
        return OrderResult(
            success=False,
            order_id="",
            symbol=symbol,
            side="buy",
            price=0,
            size=0,
            error="Hyperliquid does not support spot trading",
        )

    async def sell_spot(self, symbol: str, size: float) -> OrderResult:
        return OrderResult(
            success=False,
            order_id="",
            symbol=symbol,
            side="sell",
            price=0,
            size=0,
            error="Hyperliquid does not support spot trading",
        )

    # ------------------------------------------------------------------
    # Perpetual futures orders
    # ------------------------------------------------------------------
    async def open_short(
        self, symbol: str, size: float, leverage: int = 1
    ) -> OrderResult:
        leverage = _clamp_leverage(leverage)
        try:
            await self.exchange.set_leverage(leverage, symbol)
            order = await self.exchange.create_market_sell_order(symbol, size)
            return OrderResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side="sell",
                price=float(order.get("average", 0) or order.get("price", 0)),
                size=float(order.get("filled", size)),
            )
        except ccxt.InsufficientFunds as exc:
            logger.error("Hyperliquid open_short insufficient funds: %s", exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="sell", price=0, size=0, error=str(exc),
            )
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid open_short(%s) failed: %s", symbol, exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="sell", price=0, size=0, error=str(exc),
            )

    async def close_short(self, symbol: str, size: float) -> OrderResult:
        try:
            order = await self.exchange.create_market_buy_order(
                symbol, size, params={"reduceOnly": True}
            )
            return OrderResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side="buy",
                price=float(order.get("average", 0) or order.get("price", 0)),
                size=float(order.get("filled", size)),
            )
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid close_short(%s) failed: %s", symbol, exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="buy", price=0, size=0, error=str(exc),
            )

    # ------------------------------------------------------------------
    # Account / position queries
    # ------------------------------------------------------------------
    async def get_position(self, symbol: str) -> Position | None:
        try:
            positions = await self.exchange.fetch_positions([symbol])
            for pos in positions:
                size = abs(float(pos.get("contracts", 0)))
                if size == 0:
                    continue
                return Position(
                    symbol=symbol,
                    exchange=self.name,
                    side="short" if pos.get("side") == "short" else "long",
                    size=size,
                    entry_price=float(pos.get("entryPrice", 0)),
                    unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
                    margin=float(pos.get("initialMargin", 0)),
                    leverage=float(pos.get("leverage", 1)),
                )
            return None
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid get_position(%s) failed: %s", symbol, exc)
            raise

    async def get_all_positions(self) -> list[Position]:
        try:
            positions = await self.exchange.fetch_positions()
            result: list[Position] = []
            for pos in positions:
                size = abs(float(pos.get("contracts", 0)))
                if size == 0:
                    continue
                result.append(
                    Position(
                        symbol=pos["symbol"],
                        exchange=self.name,
                        side="short" if pos.get("side") == "short" else "long",
                        size=size,
                        entry_price=float(pos.get("entryPrice", 0)),
                        unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
                        margin=float(pos.get("initialMargin", 0)),
                        leverage=float(pos.get("leverage", 1)),
                    )
                )
            return result
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid get_all_positions failed: %s", exc)
            raise

    async def get_balance(self) -> dict[str, float]:
        try:
            balance = await self.exchange.fetch_balance()
            result: dict[str, float] = {}
            for asset, amt in balance.get("total", {}).items():
                val = float(amt) if amt else 0.0
                if val > 0:
                    result[asset] = val
            return result
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid get_balance failed: %s", exc)
            raise

    async def get_margin_ratio(self) -> float:
        try:
            balance = await self.exchange.fetch_balance()
            used = 0.0
            total = 0.0
            for asset, data in balance.items():
                if isinstance(data, dict):
                    used += float(data.get("used", 0) or 0)
                    total += float(data.get("total", 0) or 0)
            return used / total if total > 0 else 0.0
        except ccxt.BaseError as exc:
            logger.error("Hyperliquid get_margin_ratio failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        await self.exchange.close()
