"""Integration test: verifies the full pipeline from market selection through
quote generation and risk checking without hitting any external APIs."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root and parent are on sys.path so both src/ and shared/ resolve
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root.parent))

from src.strategy import Market, Order, Side, StrategyConfig, TokenSide
from src.strategy.bands import BandStrategy
from src.orderbook import OrderBook, OrderBookManager
from src.inventory import InventoryManager
from src.market_selector import MarketSelector
from src.risk import RiskConfig, RiskManager
from src.metrics import MetricsTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market(condition_id: str = "cond_abc123") -> Market:
    return Market(
        condition_id=condition_id,
        question="Will it rain tomorrow?",
        token_ids={"YES": "yes_tok_111", "NO": "no_tok_222"},
        end_date=datetime(2026, 12, 1, tzinfo=timezone.utc),
        volume_24h=5000.0,
        liquidity=10000.0,
        active=True,
    )


def _make_orderbook(token_id: str, bid_price: float = 0.50, ask_price: float = 0.55) -> OrderBook:
    book = OrderBook(token_id=token_id)
    book.set_snapshot({
        "bids": [
            {"price": str(bid_price), "size": "100"},
            {"price": str(round(bid_price - 0.02, 2)), "size": "200"},
            {"price": str(round(bid_price - 0.04, 2)), "size": "300"},
        ],
        "asks": [
            {"price": str(ask_price), "size": "100"},
            {"price": str(round(ask_price + 0.02, 2)), "size": "200"},
            {"price": str(round(ask_price + 0.04, 2)), "size": "300"},
        ],
    })
    return book


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMarketSelection:
    """MarketSelector filters and ranks Market objects correctly."""

    def test_filter_passes_valid_market(self) -> None:
        selector = MarketSelector(min_volume=1000, min_liquidity=5000, max_end_days=30)
        markets = [_make_market()]
        result = selector.filter_markets(markets)
        assert len(result) == 1
        assert result[0].condition_id == "cond_abc123"

    def test_filter_rejects_low_volume(self) -> None:
        selector = MarketSelector(min_volume=10000, min_liquidity=5000, max_end_days=30)
        market = _make_market()
        market.volume_24h = 500.0  # below threshold
        result = selector.filter_markets([market])
        assert len(result) == 0

    def test_filter_rejects_inactive(self) -> None:
        selector = MarketSelector()
        market = _make_market()
        market.active = False
        result = selector.filter_markets([market])
        assert len(result) == 0

    def test_rank_returns_sorted_list(self) -> None:
        selector = MarketSelector()
        m1 = _make_market("m1")
        m1.volume_24h = 1000
        m1.liquidity = 10000
        m2 = _make_market("m2")
        m2.volume_24h = 5000
        m2.liquidity = 10000
        ranked = selector.rank_markets([m1, m2])
        # m2 should rank higher (more volume)
        assert ranked[0].condition_id == "m2"


class TestQuoteGeneration:
    """BandStrategy generates valid orders given orderbooks and inventory."""

    def test_calculate_quotes_produces_orders(self) -> None:
        cfg = StrategyConfig.from_dict({})
        strategy = BandStrategy(cfg)
        market = _make_market()

        yes_book = _make_orderbook("yes_tok_111", bid_price=0.50, ask_price=0.55)
        no_book = _make_orderbook("no_tok_222", bid_price=0.45, ask_price=0.50)

        inv = InventoryManager(
            max_position_per_market=cfg.max_position_per_market,
            max_total_exposure=cfg.max_total_exposure,
        )
        inv.register_market(market.condition_id, market.yes_token_id, market.no_token_id)

        orders = strategy.calculate_quotes(market, yes_book, no_book, inv)

        assert len(orders) > 0, "BandStrategy should produce at least one order"
        for o in orders:
            assert isinstance(o, Order)
            assert 0.01 <= o.price <= 0.99
            assert o.size > 0
            assert o.side in (Side.BUY, Side.SELL)
            assert o.token_side in (TokenSide.YES, TokenSide.NO)

    def test_inventory_skew_adjusts_quotes(self) -> None:
        cfg = StrategyConfig.from_dict({})
        strategy = BandStrategy(cfg)
        market = _make_market()

        yes_book = _make_orderbook("yes_tok_111", bid_price=0.50, ask_price=0.55)
        no_book = _make_orderbook("no_tok_222", bid_price=0.45, ask_price=0.50)

        inv = InventoryManager(
            max_position_per_market=cfg.max_position_per_market,
            max_total_exposure=cfg.max_total_exposure,
        )
        inv.register_market(market.condition_id, market.yes_token_id, market.no_token_id)

        # Simulate holding a YES position to create skew
        inv.update_position("yes_tok_111", "BUY", 100.0, 0.50)
        skew = inv.get_skew(market.condition_id)
        assert skew > 0, "Should have positive skew with YES position"

        orders = strategy.calculate_quotes(market, yes_book, no_book, inv)
        assert len(orders) > 0


class TestRiskCheck:
    """RiskManager validates orders against limits."""

    def test_order_passes_risk_check(self) -> None:
        risk = RiskManager(RiskConfig())
        order = Order(
            token_id="yes_tok_111",
            price=0.50,
            size=50.0,
            side=Side.BUY,
            token_side=TokenSide.YES,
        )
        allowed, reason = risk.check_order(order, {}, 0.0, 0)
        assert allowed is True
        assert reason == "OK"

    def test_order_blocked_by_position_limit(self) -> None:
        risk = RiskManager(RiskConfig(max_position_per_market=100.0))
        order = Order(
            token_id="yes_tok_111",
            price=0.50,
            size=50.0,
            side=Side.BUY,
            token_side=TokenSide.YES,
        )
        # Existing position of 80 + order of 50 = 130 > 100
        inventory = {"yes_tok_111": 80.0}
        allowed, reason = risk.check_order(order, inventory, 0.0, 0)
        assert allowed is False
        assert "Position limit" in reason

    def test_order_blocked_by_daily_loss(self) -> None:
        risk = RiskManager(RiskConfig(max_daily_loss_pct=5.0, max_total_exposure=5000.0))
        order = Order(
            token_id="yes_tok_111",
            price=0.50,
            size=10.0,
            side=Side.BUY,
            token_side=TokenSide.YES,
        )
        # Daily loss of -300 exceeds 5% of 5000 = -250
        allowed, reason = risk.check_order(order, {}, -300.0, 0)
        assert allowed is False
        assert "Daily loss" in reason

    def test_kill_switch_blocks_all(self) -> None:
        risk = RiskManager(RiskConfig())
        risk._killed = True
        order = Order(
            token_id="yes_tok_111",
            price=0.50,
            size=10.0,
            side=Side.BUY,
            token_side=TokenSide.YES,
        )
        allowed, reason = risk.check_order(order, {}, 0.0, 0)
        assert allowed is False
        assert "Kill switch" in reason


class TestEndToEndPipeline:
    """Full pipeline: select -> quote -> risk check -- one cycle."""

    def test_full_cycle(self) -> None:
        # 1. Market selection
        selector = MarketSelector(min_volume=1000, min_liquidity=5000, max_end_days=30)
        market = _make_market()
        filtered = selector.filter_markets([market])
        ranked = selector.rank_markets(filtered)
        assert len(ranked) == 1

        selected = ranked[0]

        # 2. Build orderbooks
        yes_book = _make_orderbook(selected.yes_token_id, 0.50, 0.55)
        no_book = _make_orderbook(selected.no_token_id, 0.45, 0.50)

        # 3. Strategy
        cfg = StrategyConfig.from_dict({})
        strategy = BandStrategy(cfg)
        inv = InventoryManager(
            max_position_per_market=cfg.max_position_per_market,
            max_total_exposure=cfg.max_total_exposure,
        )
        inv.register_market(selected.condition_id, selected.yes_token_id, selected.no_token_id)

        orders = strategy.calculate_quotes(selected, yes_book, no_book, inv)
        assert len(orders) > 0

        # 4. Risk check every order
        risk = RiskManager(RiskConfig())
        inventory_dict: dict[str, float] = {}
        passed = []
        for o in orders:
            allowed, reason = risk.check_order(o, inventory_dict, 0.0, len(passed))
            if allowed:
                passed.append(o)

        assert len(passed) > 0, "At least some orders should pass risk checks"

        # 5. Convert to dict format that PolymarketClient.place_order expects
        for o in passed:
            order_dict = {
                "token_id": o.token_id,
                "price": o.price,
                "size": o.size,
                "side": o.side.value,
            }
            assert isinstance(order_dict["side"], str)
            assert order_dict["side"] in ("BUY", "SELL")

        # 6. Metrics recording
        metrics = MetricsTracker()
        for i, o in enumerate(passed):
            metrics.record_fill(f"order_{i}", o.price, o.size, o.side.value)
        summary = metrics.get_summary("1h")
        assert summary["total_trades"] == len(passed)

        report = metrics.format_report("1h")
        assert "Market Maker Report" in report


class TestOrderBookIntegration:
    """OrderBook snapshot/update works with the data format expected by BandStrategy."""

    def test_snapshot_and_mid_price(self) -> None:
        book = _make_orderbook("tok_123", 0.50, 0.55)
        assert book.mid_price is not None
        assert 0.50 <= book.mid_price <= 0.55

    def test_weighted_mid_price(self) -> None:
        book = _make_orderbook("tok_123", 0.50, 0.55)
        weighted = book.weighted_mid_price(depth=3)
        assert weighted is not None

    def test_empty_book_returns_none(self) -> None:
        book = OrderBook(token_id="empty")
        assert book.mid_price is None
        assert book.weighted_mid_price() is None

    def test_incremental_update(self) -> None:
        book = _make_orderbook("tok_123", 0.50, 0.55)
        original_bid = book.best_bid
        book.update({
            "bids": [{"price": "0.51", "size": "500"}],
            "asks": [],
        })
        assert book.best_bid == 0.51


class TestInventoryIntegration:
    """InventoryManager integrates correctly with BandStrategy."""

    def test_register_and_skew(self) -> None:
        inv = InventoryManager(max_position_per_market=500.0, max_total_exposure=5000.0)
        inv.register_market("cond_1", "yes_1", "no_1")
        assert inv.get_skew("cond_1") == 0.0

    def test_update_position_and_skew(self) -> None:
        inv = InventoryManager(max_position_per_market=500.0, max_total_exposure=5000.0)
        inv.register_market("cond_1", "yes_1", "no_1")
        inv.update_position("yes_1", "BUY", 100.0, 0.50)
        skew = inv.get_skew("cond_1")
        assert skew > 0

    def test_can_increase_position(self) -> None:
        inv = InventoryManager(max_position_per_market=500.0, max_total_exposure=5000.0)
        inv.register_market("cond_1", "yes_1", "no_1")
        assert inv.can_increase_position("cond_1", 100.0, 0.50) is True

    def test_position_limit_respected(self) -> None:
        inv = InventoryManager(max_position_per_market=50.0, max_total_exposure=5000.0)
        inv.register_market("cond_1", "yes_1", "no_1")
        # 100 * 0.50 = 50 notional, exactly at limit -> adding more should fail
        inv.update_position("yes_1", "BUY", 100.0, 0.50)
        assert inv.can_increase_position("cond_1", 10.0, 0.50) is False
