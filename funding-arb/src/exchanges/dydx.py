"""dYdX exchange adapter — L2 DEX, 1-hour funding intervals."""

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

FUNDING_INTERVAL_HOURS = 1  # Hourly funding


class DydxExchange(BaseExchange):
    """dYdX v4 — perps-only, cross-margin model."""

    name = "dydx"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        passphrase: str | None = None,
    ) -> None:
        super().__init__(api_key, api_secret, testnet)
        self.passphrase = passphrase

        opts: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }
        if passphrase:
            opts["password"] = passphrase

        self.exchange = ccxt.dydx(opts)

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
            logger.error("dYdX get_funding_rate(%s) failed: %s", symbol, exc)
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
            logger.error("dYdX get_all_funding_rates failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------
    async def get_spot_price(self, symbol: str) -> float:
        """dYdX is perps-only; fall back to futures price."""
        return await self.get_futures_price(symbol)

    async def get_futures_price(self, symbol: str) -> float:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except ccxt.BaseError as exc:
            logger.error("dYdX get_futures_price(%s) failed: %s", symbol, exc)
            raise

    # ------------------------------------------------------------------
    # Spot orders — not supported on dYdX
    # ------------------------------------------------------------------
    async def buy_spot(self, symbol: str, size: float) -> OrderResult:
        return OrderResult(
            success=False,
            order_id="",
            symbol=symbol,
            side="buy",
            price=0,
            size=0,
            error="dYdX does not support spot trading",
        )

    async def sell_spot(self, symbol: str, size: float) -> OrderResult:
        return OrderResult(
            success=False,
            order_id="",
            symbol=symbol,
            side="sell",
            price=0,
            size=0,
            error="dYdX does not support spot trading",
        )

    # ------------------------------------------------------------------
    # Perpetual futures orders
    # ------------------------------------------------------------------
    async def open_short(
        self, symbol: str, size: float, leverage: int = 1
    ) -> OrderResult:
        leverage = _clamp_leverage(leverage)
        try:
            # dYdX uses cross-margin; leverage is set per-account or implied
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
            logger.error("dYdX open_short insufficient funds: %s", exc)
            return OrderResult(
                success=False, order_id="", symbol=symbol,
                side="sell", price=0, size=0, error=str(exc),
            )
        except ccxt.BaseError as exc:
            logger.error("dYdX open_short(%s) failed: %s", symbol, exc)
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
            logger.error("dYdX close_short(%s) failed: %s", symbol, exc)
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
            logger.error("dYdX get_position(%s) failed: %s", symbol, exc)
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
            logger.error("dYdX get_all_positions failed: %s", exc)
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
            logger.error("dYdX get_balance failed: %s", exc)
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
            logger.error("dYdX get_margin_ratio failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        await self.exchange.close()
