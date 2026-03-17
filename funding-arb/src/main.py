"""Main loop for the Funding Rate Arbitrage Bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from pathlib import Path

# Path setup for shared utilities
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from shared.logger import get_logger
    from shared.config import DRY_RUN
    from shared.alerts import send_alert
except ImportError:
    # Fallback if shared isn't available
    DRY_RUN = True

    async def send_alert(msg: str, **kwargs: object) -> bool:
        print(f"[ALERT] {msg}")
        return True

    def get_logger(name: str, level: str = "INFO") -> logging.Logger:
        _logger = logging.getLogger(name)
        if not _logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            _logger.addHandler(handler)
            _logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        return _logger


# Import exchange layer — graceful fallback with mocks
try:
    from src.exchanges.base import BaseExchange, FundingRate, OrderResult
    from src.exchanges.factory import create_exchange, create_all_exchanges

    _EXCHANGES_AVAILABLE = True
except ImportError:
    BaseExchange = None  # type: ignore[assignment,misc]
    _EXCHANGES_AVAILABLE = False

from src.scanner import FundingScanner, Opportunity
from src.engine import ArbEngine, ArbPosition, EntryDecision, ExitDecision
from src.executor import TradeExecutor, ExitResult
from src.monitor import PositionMonitor, HealthAlert
from src.rebalancer import Rebalancer

logger = get_logger("funding-arb")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCAN_INTERVAL_SECONDS = 5 * 60  # 5 minutes
STATUS_INTERVAL_SECONDS = 60 * 60  # 1 hour

ENGINE_CONFIG = {
    "min_annualized_rate": 0.12,
    "max_positions": 10,
    "position_size_usd": 100.0,
    "max_leverage": 3,
    "exit_rate_threshold": 0.0,
    "rate_lookback_hours": 24,
}


# ---------------------------------------------------------------------------
# Mock exchange for development when Agent F's code isn't ready
# ---------------------------------------------------------------------------
class MockExchange:
    """Minimal mock for development/testing without live exchange adapters."""

    name: str = "mock"

    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self._mock_rates = [
            {
                "symbol": "BTC/USDT",
                "rate": 0.0003,
                "interval": 8,
                "price": 65000.0,
            },
            {
                "symbol": "ETH/USDT",
                "rate": 0.0002,
                "interval": 8,
                "price": 3500.0,
            },
            {
                "symbol": "SOL/USDT",
                "rate": 0.0005,
                "interval": 8,
                "price": 145.0,
            },
        ]

    async def get_all_funding_rates(self) -> list:
        from src.exchanges.base import FundingRate as FR

        return [
            FR(
                symbol=m["symbol"],
                exchange=self.name,
                rate=m["rate"],
                next_funding_time=time.time() + 3600,
                interval_hours=m["interval"],
                annualized_rate=m["rate"] * (8760 / m["interval"]),
            )
            for m in self._mock_rates
        ]

    async def get_funding_rate(self, symbol: str) -> object:
        from src.exchanges.base import FundingRate as FR

        for m in self._mock_rates:
            if m["symbol"] == symbol:
                return FR(
                    symbol=symbol,
                    exchange=self.name,
                    rate=m["rate"],
                    next_funding_time=time.time() + 3600,
                    interval_hours=m["interval"],
                    annualized_rate=m["rate"] * (8760 / m["interval"]),
                )
        return FR(
            symbol=symbol, exchange=self.name, rate=0.0001,
            next_funding_time=time.time() + 3600, interval_hours=8,
            annualized_rate=0.0001 * 1095,
        )

    async def get_spot_price(self, symbol: str) -> float:
        for m in self._mock_rates:
            if m["symbol"] == symbol:
                return m["price"]
        return 100.0

    async def get_futures_price(self, symbol: str) -> float:
        spot = await self.get_spot_price(symbol)
        return spot * 1.001  # slight contango

    async def get_balance(self) -> dict[str, float]:
        return {"USDT": 10000.0}

    async def get_margin_ratio(self) -> float:
        return 0.15

    async def get_position(self, symbol: str) -> None:
        return None

    async def get_all_positions(self) -> list:
        return []

    async def buy_spot(self, symbol: str, size: float) -> object:
        from src.exchanges.base import OrderResult as OR

        price = await self.get_spot_price(symbol)
        return OR(
            success=True, order_id="MOCK_SPOT", symbol=symbol,
            side="buy", price=price, size=size,
        )

    async def sell_spot(self, symbol: str, size: float) -> object:
        from src.exchanges.base import OrderResult as OR

        price = await self.get_spot_price(symbol)
        return OR(
            success=True, order_id="MOCK_SPOT", symbol=symbol,
            side="sell", price=price, size=size,
        )

    async def open_short(self, symbol: str, size: float, leverage: int = 1) -> object:
        from src.exchanges.base import OrderResult as OR

        price = await self.get_futures_price(symbol)
        return OR(
            success=True, order_id="MOCK_PERP", symbol=symbol,
            side="sell", price=price, size=size,
        )

    async def close_short(self, symbol: str, size: float) -> object:
        from src.exchanges.base import OrderResult as OR

        price = await self.get_futures_price(symbol)
        return OR(
            success=True, order_id="MOCK_PERP", symbol=symbol,
            side="buy", price=price, size=size,
        )

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Main bot class
# ---------------------------------------------------------------------------
class FundingArbBot:
    """Orchestrates the full funding rate arbitrage lifecycle."""

    def __init__(self) -> None:
        self.running = False
        self.positions: list[ArbPosition] = []
        self.exchanges: dict[str, BaseExchange] | dict[str, object] = {}
        self.scanner: FundingScanner | None = None
        self.engine: ArbEngine | None = None
        self.executor: TradeExecutor | None = None
        self.monitor: PositionMonitor | None = None
        self.rebalancer: Rebalancer | None = None
        self._last_status_time = 0.0

    async def initialize(self) -> None:
        """Set up all components."""
        logger.info("Initializing Funding Rate Arbitrage Bot (DRY_RUN=%s)", DRY_RUN)

        # Initialize exchanges
        if _EXCHANGES_AVAILABLE:
            try:
                self.exchanges = create_all_exchanges()
                logger.info(
                    "Loaded %d exchanges from factory", len(self.exchanges)
                )
            except Exception as exc:
                logger.warning(
                    "Factory not ready, using mocks: %s", exc
                )
                self.exchanges = self._create_mock_exchanges()
        else:
            logger.info("Exchange modules not available — using mocks")
            self.exchanges = self._create_mock_exchanges()

        # Initialize components
        self.scanner = FundingScanner(
            exchanges=self.exchanges,  # type: ignore[arg-type]
            min_rate=0.0001,
        )
        self.engine = ArbEngine(config=ENGINE_CONFIG)
        self.executor = TradeExecutor(
            exchanges=self.exchanges,  # type: ignore[arg-type]
            dry_run=DRY_RUN,
            leverage=ENGINE_CONFIG.get("max_leverage", 1),
        )
        self.monitor = PositionMonitor(
            exchanges=self.exchanges,  # type: ignore[arg-type]
            alert_fn=send_alert,
        )
        self.rebalancer = Rebalancer(
            exchanges=self.exchanges,  # type: ignore[arg-type]
            dry_run=DRY_RUN,
        )

        # TODO: load existing positions from DB
        self.positions = []

        logger.info("Initialization complete — %d exchanges ready", len(self.exchanges))

    async def run(self) -> None:
        """Main event loop."""
        self.running = True
        logger.info("Starting main loop (scan every %ds)", SCAN_INTERVAL_SECONDS)

        while self.running:
            try:
                await self._cycle()
            except Exception as exc:
                logger.error("Cycle error: %s", exc, exc_info=True)
                await send_alert(f"Funding arb cycle error: {exc}")

            # Check if it's time for a status report
            now = time.time()
            if now - self._last_status_time >= STATUS_INTERVAL_SECONDS:
                await self._send_status_report()
                self._last_status_time = now

            # Wait for next cycle
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self.running = False

        # Send final status
        await self._send_status_report()

        # Close exchange connections
        for name, exchange in self.exchanges.items():
            try:
                if hasattr(exchange, "close"):
                    await exchange.close()  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("Error closing %s: %s", name, exc)

        logger.info("Shutdown complete")

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------
    async def _cycle(self) -> None:
        """Single iteration of the main loop."""
        assert self.scanner and self.engine and self.executor and self.monitor and self.rebalancer

        # Update scanner with current positions
        existing = {f"{p.exchange}:{p.symbol}" for p in self.positions}
        self.scanner.update_existing_symbols(existing)

        # 1. Scan for opportunities
        logger.info("Scanning for opportunities...")
        opportunities = await self.scanner.scan_all()
        filtered = self.scanner.filter_opportunities(opportunities)
        logger.info(
            "Found %d opportunities (%d after filtering)",
            len(opportunities), len(filtered),
        )

        # 2. Get available capital
        available_capital = await self._get_available_capital()

        # 3. Process entry decisions
        for opp in filtered:
            decision = self.engine.should_enter(
                opp, self.positions, available_capital,
            )
            if decision.enter:
                logger.info("ENTERING: %s — %s", opp.symbol, decision.reason)
                position = await self.executor.enter_position(decision)
                if position:
                    self.positions.append(position)
                    logger.info("Position opened: %s", position)
                    await send_alert(
                        f"New position: {position.symbol}@{position.exchange} "
                        f"size={position.size:.6f}"
                    )

        # 4. Process exit decisions for existing positions
        positions_to_remove: list[ArbPosition] = []
        for position in self.positions:
            exchange = self.exchanges.get(position.exchange)
            if exchange is None:
                continue

            try:
                current_rate = await exchange.get_funding_rate(position.symbol)  # type: ignore[union-attr]
                margin_ratio = await exchange.get_margin_ratio()  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning("Failed to get data for %s: %s", position.symbol, exc)
                current_rate = None
                margin_ratio = 0.0

            exit_decision = self.engine.should_exit(
                position, current_rate, margin_ratio,
            )
            if exit_decision.exit:
                logger.info(
                    "EXITING %s: %s (urgency=%s)",
                    position.symbol, exit_decision.reason, exit_decision.urgency,
                )
                result = await self.executor.exit_position(position)
                if result.success:
                    positions_to_remove.append(position)
                    await send_alert(
                        f"Exited {position.symbol}@{position.exchange}: "
                        f"PnL=${result.total_pnl:.4f} ({exit_decision.reason})"
                    )

        # Remove exited positions
        for pos in positions_to_remove:
            self.positions = [
                p for p in self.positions
                if not (p.symbol == pos.symbol and p.exchange == pos.exchange)
            ]

        # 5. Monitor position health
        alerts = await self.monitor.check_all_positions(self.positions)
        critical = [a for a in alerts if a.level == "critical"]
        if critical:
            logger.warning("%d critical alerts!", len(critical))

        # 6. Rebalance if needed
        rebalance_actions = await self.rebalancer.check_and_rebalance(self.positions)
        for action in rebalance_actions:
            logger.info("Rebalance: %s", action)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _get_available_capital(self) -> float:
        """Sum available capital across all exchanges."""
        total = 0.0
        for name, exchange in self.exchanges.items():
            try:
                balance = await exchange.get_balance()  # type: ignore[union-attr]
                total += balance.get("USDT", 0.0) + balance.get("USD", 0.0)
            except Exception:
                pass
        return total

    async def _send_status_report(self) -> None:
        """Send periodic status report via Telegram."""
        if self.monitor:
            report = self.monitor.format_status_report(self.positions)
            logger.info("Status report:\n%s", report)
            await send_alert(report)

    @staticmethod
    def _create_mock_exchanges() -> dict[str, object]:
        """Create mock exchanges for development."""
        return {
            "binance_mock": MockExchange("binance_mock"),
            "bybit_mock": MockExchange("bybit_mock"),
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    """Entry point for the funding rate arbitrage bot."""
    bot = FundingArbBot()

    # Handle graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.shutdown()))

    try:
        await bot.initialize()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
