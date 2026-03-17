"""Main lifecycle loop for the Polymarket market maker bot."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root so shared/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from shared import config
from shared.alerts import send_alert
from shared.logger import get_logger

from src.clob import PolymarketClient
from src.risk import HealthLevel, RiskConfig, RiskManager
from src.metrics import MetricsTracker

logger = get_logger("polymarket-maker", config.LOG_LEVEL)

# ------------------------------------------------------------------
# Try to import Agent D modules; fall back to stubs
# ------------------------------------------------------------------
try:
    from src.strategy import StrategyConfig, Order, Side, TokenSide, Market
    STRATEGY_READY = True
except ImportError:
    STRATEGY_READY = False
    logger.warning("Strategy module not fully available — running with stubs")

try:
    from src.strategy.bands import BandStrategy  # type: ignore[import-untyped]
    BAND_READY = True
except ImportError:
    BAND_READY = False
    logger.warning("BandStrategy not available — using mock quotes")

try:
    from src.market_selector import MarketSelector  # type: ignore[import-untyped]
    SELECTOR_READY = True
except ImportError:
    SELECTOR_READY = False
    logger.warning("MarketSelector not available — using config whitelist")

try:
    from src.orderbook import OrderBook, OrderBookManager  # type: ignore[import-untyped]
    ORDERBOOK_READY = True
except ImportError:
    ORDERBOOK_READY = False
    logger.warning("OrderBook not available — using raw API orderbook data")

try:
    from src.inventory import InventoryManager  # type: ignore[import-untyped]
    INVENTORY_READY = True
except ImportError:
    INVENTORY_READY = False
    logger.warning("InventoryManager not available — using simple dict inventory")


# ------------------------------------------------------------------
# Config loading
# ------------------------------------------------------------------

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_strategy_config() -> dict:
    """Load strategy.json from config directory."""
    path = CONFIG_DIR / "strategy.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    logger.warning("strategy.json not found, using defaults")
    return {}


def load_markets_config() -> dict:
    """Load markets.json from config directory."""
    path = CONFIG_DIR / "markets.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    logger.warning("markets.json not found, using defaults")
    return {}


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------

class MarketMakerBot:
    """Orchestrates the full market-making lifecycle."""

    def __init__(self) -> None:
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._start_time = time.time()

        # Core components
        self.client = PolymarketClient()
        strategy_cfg = load_strategy_config()
        self.risk = RiskManager(RiskConfig.from_dict(strategy_cfg))
        self.metrics = MetricsTracker()

        # Strategy
        self._strategy_cfg = strategy_cfg
        self._refresh_interval: int = strategy_cfg.get("refresh_interval_seconds", 30)
        self._health_interval: int = 300  # 5 minutes

        # State
        self._active_markets: list[dict] = []
        self._inventory: dict[str, float] = {}
        self._open_order_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Market selection
    # ------------------------------------------------------------------

    def _select_markets(self) -> list[dict]:
        """Select markets to make on."""
        markets_cfg = load_markets_config()

        if SELECTOR_READY:
            try:
                selector = MarketSelector(
                    min_volume=markets_cfg.get("min_volume_24h", 1000.0),
                    min_liquidity=markets_cfg.get("min_liquidity", 5000.0),
                    max_end_days=markets_cfg.get("max_end_days", 30),
                    blacklist=markets_cfg.get("blacklist"),
                    whitelist=markets_cfg.get("whitelist"),
                )
                # Fetch raw markets from API and filter them
                raw_markets = self.client.get_markets()
                parsed = []
                for m in raw_markets:
                    try:
                        tokens = m.get("tokens", [])
                        token_ids = {}
                        for t in tokens:
                            outcome = t.get("outcome", "").upper()
                            if outcome in ("YES", "NO"):
                                token_ids[outcome] = t.get("token_id", "")
                        if "YES" not in token_ids or "NO" not in token_ids:
                            continue
                        end_str = m.get("end_date_iso", m.get("end_date", ""))
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else datetime.now(timezone.utc)
                        parsed.append(Market(
                            condition_id=m.get("condition_id", ""),
                            question=m.get("question", ""),
                            token_ids=token_ids,
                            end_date=end_dt,
                            volume_24h=float(m.get("volume_num_24hr", m.get("volume_24h", 0))),
                            liquidity=float(m.get("liquidity", 0)),
                            active=m.get("active", True),
                        ))
                    except Exception:
                        continue
                filtered = selector.filter_markets(parsed)
                ranked = selector.rank_markets(filtered)
                # Convert back to dicts for the rest of the pipeline
                return [
                    {
                        "condition_id": mk.condition_id,
                        "question": mk.question,
                        "tokens": [
                            {"token_id": mk.yes_token_id, "outcome": "Yes"},
                            {"token_id": mk.no_token_id, "outcome": "No"},
                        ],
                    }
                    for mk in ranked[:10]
                ]
            except Exception:
                logger.exception("MarketSelector failed, falling back")

        # Fallback: use whitelist from config or fetch from API
        whitelist = markets_cfg.get("whitelist", [])
        if whitelist:
            markets = []
            for cid in whitelist:
                m = self.client.get_market(cid)
                if m:
                    markets.append(m)
            return markets

        # Last resort: fetch first page of markets
        return self.client.get_markets()[:5]

    # ------------------------------------------------------------------
    # Quote generation
    # ------------------------------------------------------------------

    def _generate_quotes(self, market: dict) -> list[dict]:
        """Generate buy/sell quotes for a market using BandStrategy."""
        if BAND_READY and STRATEGY_READY and ORDERBOOK_READY and INVENTORY_READY:
            try:
                scfg = StrategyConfig.from_dict(self._strategy_cfg)
                strategy = BandStrategy(scfg)

                # Get token IDs
                tokens = market.get("tokens", [])
                if len(tokens) < 2:
                    return []

                yes_token = ""
                no_token = ""
                for t in tokens:
                    outcome = t.get("outcome", "").upper()
                    if outcome == "YES":
                        yes_token = t.get("token_id", "")
                    elif outcome == "NO":
                        no_token = t.get("token_id", "")

                if not yes_token or not no_token:
                    return []

                # Fetch orderbooks and build OrderBook objects
                yes_raw = self.client.get_orderbook(yes_token)
                no_raw = self.client.get_orderbook(no_token)

                yes_book = OrderBook(token_id=yes_token)
                yes_book.set_snapshot(yes_raw)
                no_book = OrderBook(token_id=no_token)
                no_book.set_snapshot(no_raw)

                # Build Market object
                cid = market.get("condition_id", "")
                market_obj = Market(
                    condition_id=cid,
                    question=market.get("question", ""),
                    token_ids={"YES": yes_token, "NO": no_token},
                    end_date=datetime.now(timezone.utc),
                )

                # Build InventoryManager and register market
                inv = InventoryManager(
                    max_position_per_market=scfg.max_position_per_market,
                    max_total_exposure=scfg.max_total_exposure,
                )
                inv.register_market(cid, yes_token, no_token)

                orders = strategy.calculate_quotes(market_obj, yes_book, no_book, inv)
                return [
                    {
                        "token_id": o.token_id,
                        "price": o.price,
                        "size": o.size,
                        "side": o.side.value,
                    }
                    for o in orders
                ]
            except Exception:
                logger.exception("BandStrategy quote generation failed")
                return []

        # Stub: no quotes if strategy modules aren't ready
        logger.debug("Strategy modules not fully available — skipping quote generation")
        return []

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    def _refresh_inventory(self) -> None:
        """Refresh position/inventory state from the API."""
        try:
            positions = self.client.get_positions()
            self._inventory = {}
            for p in positions:
                token_id = p.get("asset", p.get("token_id", ""))
                size = float(p.get("size", p.get("balance", 0)))
                if token_id:
                    self._inventory[token_id] = size
        except Exception:
            logger.exception("Failed to refresh inventory")

    def _cancel_stale_orders(self) -> int:
        """Cancel all existing open orders before refreshing."""
        count = self.client.cancel_all_orders()
        self._open_order_ids.clear()
        return count

    def _place_quotes(self, quotes: list[dict]) -> int:
        """Place a batch of quotes after risk checks. Returns count placed."""
        placed = 0
        pnl = self.risk.get_daily_pnl()
        open_count = len(self._open_order_ids)

        for q in quotes:
            # Build a minimal Order-like object for risk check
            if STRATEGY_READY:
                try:
                    order = Order(
                        token_id=q["token_id"],
                        price=q["price"],
                        size=q["size"],
                        side=Side(q["side"]),
                        token_side=TokenSide.YES,
                    )
                except Exception:
                    logger.exception("Failed to create Order object")
                    continue
            else:
                order = type("Order", (), {
                    "token_id": q["token_id"],
                    "price": q["price"],
                    "size": q["size"],
                    "side": q["side"],
                })()

            allowed, reason = self.risk.check_order(
                order, self._inventory, pnl, open_count + placed,
            )
            if not allowed:
                logger.warning("Order blocked by risk", extra={
                    "reason": reason,
                    "token_id": q["token_id"],
                    "side": q["side"],
                })
                continue

            resp = self.client.place_order(
                token_id=q["token_id"],
                price=q["price"],
                size=q["size"],
                side=q["side"],
            )
            order_id = resp.get("orderID") or resp.get("order_id", "")
            if order_id:
                self._open_order_ids.add(order_id)
                placed += 1

        return placed

    # ------------------------------------------------------------------
    # Async lifecycle
    # ------------------------------------------------------------------

    async def _trading_loop(self) -> None:
        """Core trading loop: refresh -> quote -> risk check -> place."""
        while self._running and not self._shutdown_event.is_set():
            try:
                self._refresh_inventory()

                for market in self._active_markets:
                    if not self._running:
                        break

                    quotes = self._generate_quotes(market)
                    if not quotes:
                        continue

                    cancelled = self._cancel_stale_orders()
                    placed = self._place_quotes(quotes)

                    cid = market.get("condition_id", "?")[:12]
                    logger.info("Cycle complete", extra={
                        "market": cid,
                        "cancelled": cancelled,
                        "placed": placed,
                        "inventory_tokens": len(self._inventory),
                    })

            except Exception:
                logger.exception("Error in trading loop iteration")

            # Wait for next cycle or shutdown
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._refresh_interval,
                )
            except asyncio.TimeoutError:
                pass  # Normal: timeout means time for next cycle

    def _write_health(self) -> None:
        """Write health.json for monitoring system."""
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        health = {
            "bot": "poly-maker",
            "status": "running" if self._running else "stopped",
            "uptime_seconds": int(time.time() - self._start_time),
            "pnl": self.risk.get_daily_pnl(),
            "open_orders": len(self._open_order_ids),
            "active_markets": len(self._active_markets),
            "dry_run": config.DRY_RUN,
            "timestamp": time.time(),
        }
        (data_dir / "health.json").write_text(json.dumps(health))

    def _check_kill_signal(self) -> bool:
        """Check if external kill signal exists."""
        return Path("data/KILL_SIGNAL").exists()

    async def _health_loop(self) -> None:
        """Periodic health check and Telegram reporting."""
        while self._running and not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._health_interval,
                )
            except asyncio.TimeoutError:
                pass  # Normal timeout — time to check health

            if not self._running:
                break

            # Write health file and check kill signal
            self._write_health()
            if self._check_kill_signal():
                logger.warning("Kill signal detected — shutting down")
                await self.risk.kill_switch(self.client, "External kill signal")
                self._running = False
                self._shutdown_event.set()
                break

            try:
                pnl = self.risk.get_daily_pnl()
                status = self.risk.check_health(pnl, self._inventory)
                logger.info("Health check", extra={
                    "level": status.level.value,
                    "message": status.message,
                    "action": status.action,
                })

                if status.level == HealthLevel.RED:
                    await self.risk.kill_switch(self.client, status.message)
                    self._running = False
                    self._shutdown_event.set()
                    break

                if status.level == HealthLevel.YELLOW:
                    await send_alert(
                        f"<b>Warning</b>\n{status.message}",
                    )

                # Periodic report
                report = self.metrics.format_report()
                await send_alert(report)

            except Exception:
                logger.exception("Error in health loop")

    async def _shutdown(self) -> None:
        """Graceful shutdown: cancel all orders, report final PnL."""
        logger.info("Shutting down — cancelling all orders…")
        self._running = False
        self._shutdown_event.set()

        try:
            count = self.client.cancel_all_orders()
            logger.info("Shutdown cancelled orders", extra={"count": count})
        except Exception:
            logger.exception("Failed to cancel orders during shutdown")

        pnl = self.risk.get_daily_pnl()
        report = self.metrics.format_report("24h")
        shutdown_msg = f"<b>Bot Shutdown</b>\nDaily PnL: ${pnl:.4f}\n\n{report}"

        try:
            await send_alert(shutdown_msg)
        except Exception:
            logger.exception("Failed to send shutdown alert")

        logger.info("Shutdown complete", extra={"final_pnl": pnl})

    async def run(self) -> None:
        """Main entry point: initialise, run loops, handle signals."""
        self._running = True
        logger.info("Starting Polymarket Market Maker", extra={
            "dry_run": config.DRY_RUN,
            "refresh_interval": self._refresh_interval,
        })

        # Select markets
        self._active_markets = self._select_markets()
        market_ids = [m.get("condition_id", "?")[:12] for m in self._active_markets]
        logger.info("Active markets selected", extra={
            "count": len(self._active_markets),
            "markets": market_ids,
        })

        if not self._active_markets:
            logger.error("No markets selected — exiting")
            return

        await send_alert(
            f"<b>Bot Started</b>\n"
            f"DRY_RUN: {config.DRY_RUN}\n"
            f"Markets: {len(self._active_markets)}\n"
            f"Refresh: {self._refresh_interval}s",
        )

        # Run trading + health loops concurrently
        try:
            await asyncio.gather(
                self._trading_loop(),
                self._health_loop(),
            )
        except asyncio.CancelledError:
            logger.info("Tasks cancelled")
        finally:
            await self._shutdown()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def _handle_signal(bot: MarketMakerBot, loop: asyncio.AbstractEventLoop) -> None:
    """Signal handler that triggers graceful shutdown."""
    logger.info("Received termination signal")
    bot._running = False
    bot._shutdown_event.set()


async def main() -> None:
    """Bootstrap and run the bot."""
    bot = MarketMakerBot()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, bot, loop)

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
