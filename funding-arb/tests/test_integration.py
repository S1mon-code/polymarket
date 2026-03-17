"""Integration test — verifies the full pipeline using mock exchanges."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Path setup
_project_root = Path(__file__).resolve().parent.parent
_polymarket_root = _project_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_polymarket_root))

from src.exchanges.base import BaseExchange, FundingRate, OrderResult, Position
from src.scanner import FundingScanner, Opportunity
from src.engine import ArbEngine, ArbPosition, EntryDecision, ExitDecision
from src.executor import TradeExecutor, ExitResult
from src.monitor import PositionMonitor, HealthAlert
from src.rebalancer import Rebalancer


# ---------------------------------------------------------------------------
# Mock exchange that implements BaseExchange interface (duck-typed)
# ---------------------------------------------------------------------------
class MockTestExchange:
    """Mock exchange for integration testing."""

    name: str = "mock_test"

    def __init__(self, name: str = "mock_test") -> None:
        self.name = name
        self._rates = {
            "BTC/USDT": {"rate": 0.0003, "price": 65000.0, "interval": 8},
            "ETH/USDT": {"rate": 0.0002, "price": 3500.0, "interval": 8},
            "SOL/USDT": {"rate": 0.0005, "price": 145.0, "interval": 8},
        }

    async def get_all_funding_rates(self) -> list[FundingRate]:
        result = []
        for symbol, data in self._rates.items():
            result.append(FundingRate(
                symbol=symbol,
                exchange=self.name,
                rate=data["rate"],
                next_funding_time=time.time() + 3600,
                interval_hours=data["interval"],
                annualized_rate=data["rate"] * (8760 / data["interval"]),
            ))
        return result

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        data = self._rates.get(symbol, {"rate": 0.0001, "price": 100.0, "interval": 8})
        return FundingRate(
            symbol=symbol,
            exchange=self.name,
            rate=data["rate"],
            next_funding_time=time.time() + 3600,
            interval_hours=data["interval"],
            annualized_rate=data["rate"] * (8760 / data["interval"]),
        )

    async def get_spot_price(self, symbol: str) -> float:
        data = self._rates.get(symbol)
        return data["price"] if data else 100.0

    async def get_futures_price(self, symbol: str) -> float:
        spot = await self.get_spot_price(symbol)
        return spot * 1.001

    async def get_balance(self) -> dict[str, float]:
        return {"USDT": 10000.0}

    async def get_margin_ratio(self) -> float:
        return 0.15

    async def get_position(self, symbol: str) -> Position | None:
        return None

    async def get_all_positions(self) -> list[Position]:
        return []

    async def buy_spot(self, symbol: str, size: float) -> OrderResult:
        price = await self.get_spot_price(symbol)
        return OrderResult(
            success=True, order_id="TEST_SPOT", symbol=symbol,
            side="buy", price=price, size=size,
        )

    async def sell_spot(self, symbol: str, size: float) -> OrderResult:
        price = await self.get_spot_price(symbol)
        return OrderResult(
            success=True, order_id="TEST_SPOT", symbol=symbol,
            side="sell", price=price, size=size,
        )

    async def open_short(self, symbol: str, size: float, leverage: int = 1) -> OrderResult:
        price = await self.get_futures_price(symbol)
        return OrderResult(
            success=True, order_id="TEST_PERP", symbol=symbol,
            side="sell", price=price, size=size,
        )

    async def close_short(self, symbol: str, size: float) -> OrderResult:
        price = await self.get_futures_price(symbol)
        return OrderResult(
            success=True, order_id="TEST_PERP", symbol=symbol,
            side="buy", price=price, size=size,
        )

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_dataclass_compatibility():
    """Verify Agent F dataclasses have all fields Agent G expects."""
    # FundingRate fields used by scanner
    fr = FundingRate(
        symbol="BTC/USDT", exchange="test", rate=0.0003,
        next_funding_time=time.time(), interval_hours=8,
        annualized_rate=0.0003 * 1095,
    )
    assert hasattr(fr, "symbol")
    assert hasattr(fr, "exchange")
    assert hasattr(fr, "rate")
    assert hasattr(fr, "annualized_rate")
    assert hasattr(fr, "next_funding_time")
    assert hasattr(fr, "interval_hours")

    # OrderResult fields used by executor
    order = OrderResult(
        success=True, order_id="test", symbol="BTC/USDT",
        side="buy", price=65000.0, size=0.001,
    )
    assert hasattr(order, "success")
    assert hasattr(order, "price")
    assert hasattr(order, "size")

    # Position fields used by monitor
    pos = Position(
        symbol="BTC/USDT", exchange="test", side="short",
        size=0.001, entry_price=65000.0, unrealized_pnl=0.0,
        margin=100.0, leverage=1.0,
    )
    assert hasattr(pos, "symbol")
    assert hasattr(pos, "exchange")
    assert hasattr(pos, "side")
    assert hasattr(pos, "size")
    assert hasattr(pos, "unrealized_pnl")

    print("PASS: dataclass_compatibility")


def test_full_pipeline():
    """End-to-end: scan -> engine -> executor -> monitor -> exit."""

    async def _run():
        # Setup
        exchanges = {
            "exchange_a": MockTestExchange("exchange_a"),
            "exchange_b": MockTestExchange("exchange_b"),
        }

        scanner = FundingScanner(exchanges=exchanges, min_rate=0.0001)
        engine = ArbEngine(config={
            "min_annualized_rate": 0.10,
            "max_positions": 5,
            "position_size_usd": 100.0,
            "max_leverage": 3,
        })
        executor = TradeExecutor(exchanges=exchanges, dry_run=True, leverage=1)
        monitor = PositionMonitor(exchanges=exchanges)
        rebalancer = Rebalancer(exchanges=exchanges, dry_run=True)

        # Step 1: Scan
        opportunities = await scanner.scan_all()
        assert len(opportunities) > 0, "Scanner should find opportunities"
        print(f"  Scanner found {len(opportunities)} opportunities")

        # Step 2: Filter
        filtered = scanner.filter_opportunities(opportunities)
        assert len(filtered) > 0, "Should have filtered opportunities"
        print(f"  Filtered to {len(filtered)} actionable opportunities")

        # Step 3: Engine decides entry
        best = filtered[0]
        decision = engine.should_enter(best, [], available_capital=10000.0)
        assert decision.enter, f"Engine should approve entry, got: {decision.reason}"
        assert decision.size > 0, "Decision size must be positive"
        print(f"  Engine approved: {best.symbol} size={decision.size:.6f}")

        # Step 4: Execute entry (dry run)
        position = await executor.enter_position(decision)
        assert position is not None, "Executor should return a position"
        assert position.symbol == best.symbol
        assert position.size == decision.size
        print(f"  Executor opened: {position}")

        # Step 5: Monitor health
        alerts = await monitor.check_all_positions([position])
        # With mock exchange returning healthy values, expect no critical alerts
        critical = [a for a in alerts if a.level == "critical"]
        assert len(critical) == 0, f"Unexpected critical alerts: {critical}"
        print(f"  Monitor returned {len(alerts)} alerts (0 critical)")

        # Step 6: Rebalance check
        actions = await rebalancer.check_and_rebalance([position])
        assert len(actions) > 0, "Rebalancer should return status"
        print(f"  Rebalancer: {actions}")

        # Step 7: Engine decides exit (simulate rate turning negative)
        negative_rate = FundingRate(
            symbol=position.symbol, exchange=position.exchange,
            rate=-0.001, next_funding_time=time.time() + 3600,
            interval_hours=8, annualized_rate=-0.001 * 1095,
        )
        exit_decision = engine.should_exit(position, negative_rate, margin_ratio=0.15)
        assert exit_decision.exit, "Engine should approve exit on negative rate"
        print(f"  Engine exit: {exit_decision.reason}")

        # Step 8: Execute exit (dry run)
        exit_result = await executor.exit_position(position)
        assert exit_result.success, "Exit should succeed"
        print(f"  Executor exited: {exit_result}")

        # Verify positions cleaned up
        assert len(executor.positions) == 0, "All positions should be closed"
        print("  Positions clean: 0 remaining")

    asyncio.run(_run())
    print("PASS: full_pipeline")


def test_cross_exchange_scan():
    """Verify cross-exchange scanning works with different rates."""

    async def _run():
        # Two exchanges with different rates
        ex_a = MockTestExchange("exchange_a")
        ex_b = MockTestExchange("exchange_b")
        # Make exchange_b have higher rates
        for sym in ex_b._rates:
            ex_b._rates[sym]["rate"] = ex_b._rates[sym]["rate"] * 3

        exchanges = {"exchange_a": ex_a, "exchange_b": ex_b}
        scanner = FundingScanner(exchanges=exchanges, min_rate=0.0001)

        cross_opps = await scanner.scan_cross_exchange()
        assert len(cross_opps) > 0, "Should find cross-exchange opportunities"
        print(f"  Found {len(cross_opps)} cross-exchange opportunities")

        # Verify the structure
        opp = cross_opps[0]
        assert opp.long_exchange != opp.short_exchange
        assert opp.rate_differential > 0
        assert opp.annualized_return > 0
        print(f"  Best: {opp}")

    asyncio.run(_run())
    print("PASS: cross_exchange_scan")


def test_leverage_safety():
    """Verify leverage is never allowed above 3x."""

    engine = ArbEngine(config={"max_leverage": 10})
    assert engine.config["max_leverage"] == 3, "Leverage should be clamped to 3"

    executor = TradeExecutor(exchanges={}, dry_run=True, leverage=5)
    assert executor.leverage == 3, "Executor leverage should be clamped to 3"

    print("PASS: leverage_safety")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Funding Rate Arb — Integration Tests")
    print("=" * 60)

    tests = [
        test_dataclass_compatibility,
        test_leverage_safety,
        test_full_pipeline,
        test_cross_exchange_scan,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            print(f"\nRunning {test_fn.__name__}...")
            test_fn()
            passed += 1
        except Exception as exc:
            print(f"FAIL: {test_fn.__name__} — {exc}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
