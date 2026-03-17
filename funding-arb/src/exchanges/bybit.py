"""Bybit exchange adapter — unified account, spot + linear futures via ccxt.bybit."""

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

FUNDING_INTERVAL_HOURS = 8


class BybitExchange(BaseExchange):
    name = "bybit"

    def __init__(
        self, api_key: str, api_secret: str, testnet: bool = False
    ) -> None:
        super().__init__(api_key, api_secret, testnet)

        opts: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }

        # Spot client
        self.spot = ccxt.bybit({**opts, "options": {"defaultType": "spot"}})
        # Linear futures client
        self.futures = ccxt.bybit({**opts, "options": {"defaultType": "linear"}})

        if testnet:
            self.spot.set_sandbox_mode(True)
            self.futures.set_sandbox_mode(True)

    # ------------------------------------------------------------------
    # Funding rates
    # ------------------------------------------------------------------
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        try:
            data = await self.futures.fetch_funding_rate(symbol)
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
            logger.error("Bybit get_funding_rate(%s) failed: %s", symbol, exc)
            raise

    async def get_all_funding_rates(self) -> list[FundingRate]:
        try:
            rates = await self.futures.fetch_funding_rates()
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
            logger.error("Bybit get_all_funding_rates failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------
    async def get_spot_price(self, symbol: str) -> float:
        try:
            ticker = await self.spot.fetch_ticker(symbol)
            return float(ticker["last"])
        except ccxt.BaseError as exc:
            logger.error("Bybit get_spot_price(%s) failed: %s", symbol, exc)
            raise

    async def get_futures_price(self, symbol: str) -> float:
        try:
            ticker = await self.futures.fetch_ticker(symbol)
            return float(ticker["last"])
        except ccxt.BaseError as exc:
            logger.error("Bybit get_futures_price(%s) failed: %s", symbol, exc)
            raise

    # ------------------------------------------------------------------
    # Spot orders
    # ------------------------------------------------------------------
    async def buy_spot(self, symbol: str, size: float) -> OrderResult:
        try:
            order = await self.spot.create_market_buy_order(symbol, size)
            return OrderResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side="buy",
                price=float(order.get("average", 0) or order.get("price", 0)),
                size=float(order.get("filled", size)),
            )
        except ccxt.InsufficientFunds as exc:
            logger.error("Bybit buy_spot insufficient funds: %s", exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="buy", price=0, size=0, error=str(exc),
            )
        except ccxt.BaseError as exc:
            logger.error("Bybit buy_spot(%s) failed: %s", symbol, exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="buy", price=0, size=0, error=str(exc),
            )

    async def sell_spot(self, symbol: str, size: float) -> OrderResult:
        try:
            order = await self.spot.create_market_sell_order(symbol, size)
            return OrderResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side="sell",
                price=float(order.get("average", 0) or order.get("price", 0)),
                size=float(order.get("filled", size)),
            )
        except ccxt.InsufficientFunds as exc:
            logger.error("Bybit sell_spot insufficient funds: %s", exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="sell", price=0, size=0, error=str(exc),
            )
        except ccxt.BaseError as exc:
            logger.error("Bybit sell_spot(%s) failed: %s", symbol, exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="sell", price=0, size=0, error=str(exc),
            )

    # ------------------------------------------------------------------
    # Perpetual futures orders
    # ------------------------------------------------------------------
    async def open_short(
        self, symbol: str, size: float, leverage: int = 1
    ) -> OrderResult:
        leverage = _clamp_leverage(leverage)
        try:
            await self.futures.set_leverage(leverage, symbol)
            order = await self.futures.create_market_sell_order(symbol, size)
            return OrderResult(
                success=True,
                order_id=str(order["id"]),
                symbol=symbol,
                side="sell",
                price=float(order.get("average", 0) or order.get("price", 0)),
                size=float(order.get("filled", size)),
            )
        except ccxt.InsufficientFunds as exc:
            logger.error("Bybit open_short insufficient funds: %s", exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="sell", price=0, size=0, error=str(exc),
            )
        except ccxt.BaseError as exc:
            logger.error("Bybit open_short(%s) failed: %s", symbol, exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="sell", price=0, size=0, error=str(exc),
            )

    async def close_short(self, symbol: str, size: float) -> OrderResult:
        try:
            order = await self.futures.create_market_buy_order(
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
            logger.error("Bybit close_short(%s) failed: %s", symbol, exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="buy", price=0, size=0, error=str(exc),
            )

    # ------------------------------------------------------------------
    # Account / position queries
    # ------------------------------------------------------------------
    async def get_position(self, symbol: str) -> Position | None:
        try:
            positions = await self.futures.fetch_positions([symbol])
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
            logger.error("Bybit get_position(%s) failed: %s", symbol, exc)
            raise

    async def get_all_positions(self) -> list[Position]:
        try:
            positions = await self.futures.fetch_positions()
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
            logger.error("Bybit get_all_positions failed: %s", exc)
            raise

    async def get_balance(self) -> dict[str, float]:
        try:
            balance = await self.futures.fetch_balance()
            result: dict[str, float] = {}
            for asset, amt in balance.get("total", {}).items():
                val = float(amt) if amt else 0.0
                if val > 0:
                    result[asset] = val
            return result
        except ccxt.BaseError as exc:
            logger.error("Bybit get_balance failed: %s", exc)
            raise

    async def get_margin_ratio(self) -> float:
        try:
            balance = await self.futures.fetch_balance()
            # Bybit unified account: accountIMRate from info
            info = balance.get("info", {})
            # Try unified account fields
            if isinstance(info, dict) and "result" in info:
                acct = info["result"]
                if isinstance(acct, dict) and "list" in acct:
                    for item in acct["list"]:
                        im_rate = float(item.get("accountIMRate", 0))
                        return im_rate
            # Fallback: compute from margin / equity
            total_margin = 0.0
            total_equity = 0.0
            for asset, data in balance.items():
                if isinstance(data, dict):
                    total_margin += float(data.get("used", 0) or 0)
                    total_equity += float(data.get("total", 0) or 0)
            return total_margin / total_equity if total_equity > 0 else 0.0
        except ccxt.BaseError as exc:
            logger.error("Bybit get_margin_ratio failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        await self.spot.close()
        await self.futures.close()
