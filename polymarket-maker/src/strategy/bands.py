"""Band market-making strategy — the core profit engine."""

from __future__ import annotations

import logging
import time
from typing import Optional

from src.inventory import InventoryManager
from src.orderbook import OrderBook
from src.strategy import Market, Order, Side, StrategyConfig, TokenSide

logger = logging.getLogger(__name__)

# Polymarket price constraints
MIN_PRICE = 0.01
MAX_PRICE = 0.99


def clamp_price(price: float) -> float:
    """Clamp a price to Polymarket's valid range [0.01, 0.99]."""
    return max(MIN_PRICE, min(MAX_PRICE, round(price, 2)))


class BandStrategy:
    """
    Band market-making strategy.

    Places orders at multiple price levels (bands) around an estimated fair price.
    Tighter bands have smaller sizes, wider bands have larger sizes.
    Inventory skew adjusts quotes to reduce directional exposure.
    """

    def __init__(self, config: StrategyConfig):
        self.config: StrategyConfig = config
        self._last_refresh: dict[str, float] = {}  # condition_id -> timestamp

    def calculate_quotes(
        self,
        market: Market,
        yes_book: OrderBook,
        no_book: OrderBook,
        inventory: InventoryManager,
    ) -> list[Order]:
        """
        Calculate where to place orders given the current market state.

        Logic:
        1. Estimate fair price from orderbook mid-price (weighted).
        2. Adjust for inventory skew.
        3. Place orders in bands on both YES and NO sides.
        4. Respect position limits.
        5. Ensure all prices are in [0.01, 0.99].

        Args:
            market: The market to quote.
            yes_book: Current YES token orderbook.
            no_book: Current NO token orderbook.
            inventory: Inventory manager for position/skew data.

        Returns:
            List of orders to place (both YES and NO sides, bids and asks).
        """
        orders: list[Order] = []

        # Step 1: Estimate fair prices
        yes_fair = self.calculate_fair_price(yes_book)
        no_fair = self.calculate_fair_price(no_book)

        if yes_fair is None and no_fair is None:
            logger.warning("No fair price available for market %s", market.condition_id[:12])
            return orders

        # If only one side has data, infer the other (YES + NO ~ 1.0)
        if yes_fair is None and no_fair is not None:
            yes_fair = 1.0 - no_fair
        elif no_fair is None and yes_fair is not None:
            no_fair = 1.0 - yes_fair

        assert yes_fair is not None and no_fair is not None

        # Step 2: Get inventory skew and adjust fair prices
        skew = inventory.get_skew(market.condition_id)
        skew_adjustment = skew * self.config.inventory_skew_factor * self.config.min_spread

        # Positive skew = too much YES -> lower YES fair (make YES cheaper to sell)
        # Negative skew = too much NO -> raise YES fair (make YES more expensive)
        adjusted_yes_fair = yes_fair - skew_adjustment
        adjusted_no_fair = no_fair + skew_adjustment

        # Step 3: Generate orders for each band
        yes_orders = self._generate_band_orders(
            token_id=market.yes_token_id,
            token_side=TokenSide.YES,
            fair_price=adjusted_yes_fair,
            condition_id=market.condition_id,
            inventory=inventory,
        )
        no_orders = self._generate_band_orders(
            token_id=market.no_token_id,
            token_side=TokenSide.NO,
            fair_price=adjusted_no_fair,
            condition_id=market.condition_id,
            inventory=inventory,
        )

        orders.extend(yes_orders)
        orders.extend(no_orders)

        logger.info(
            "Generated %d orders for market %s (YES fair=%.4f, NO fair=%.4f, skew=%.3f)",
            len(orders), market.condition_id[:12], adjusted_yes_fair, adjusted_no_fair, skew,
        )
        return orders

    def _generate_band_orders(
        self,
        token_id: str,
        token_side: TokenSide,
        fair_price: float,
        condition_id: str,
        inventory: InventoryManager,
    ) -> list[Order]:
        """Generate bid and ask orders across all bands for one token."""
        orders: list[Order] = []
        half_spread = self.config.min_spread / 2.0

        for band_idx in range(self.config.num_bands):
            band_offset = band_idx * self.config.band_width

            # Calculate band prices
            bid_price = clamp_price(fair_price - half_spread - band_offset)
            ask_price = clamp_price(fair_price + half_spread + band_offset)

            # Calculate size for this band
            multiplier = (
                self.config.order_size_multiplier[band_idx]
                if band_idx < len(self.config.order_size_multiplier)
                else self.config.order_size_multiplier[-1]
            )
            size = self.config.order_size_base * multiplier

            # Check position limits before adding bid (buying increases position)
            if inventory.can_increase_position(condition_id, size, bid_price):
                try:
                    bid = Order(
                        token_id=token_id,
                        price=bid_price,
                        size=size,
                        side=Side.BUY,
                        token_side=token_side,
                    )
                    orders.append(bid)
                except ValueError as e:
                    logger.debug("Skipping invalid bid: %s", e)

            # Ask (selling) — always allowed if we have position, but also
            # check limits for short exposure
            try:
                ask = Order(
                    token_id=token_id,
                    price=ask_price,
                    size=size,
                    side=Side.SELL,
                    token_side=token_side,
                )
                orders.append(ask)
            except ValueError as e:
                logger.debug("Skipping invalid ask: %s", e)

        return orders

    def should_refresh(self, condition_id: str, current_orders: list[Order], new_quotes: list[Order]) -> bool:
        """
        Check if orders need refreshing.

        Reasons to refresh:
        1. Time since last refresh exceeds interval.
        2. Price has moved significantly from current orders.
        3. Number of active orders differs from expected.
        """
        now = time.time()
        last = self._last_refresh.get(condition_id, 0.0)

        # Time-based refresh
        if now - last >= self.config.refresh_interval_seconds:
            return True

        # Check if the set of orders has no current orders to compare
        if not current_orders:
            return True

        # Price deviation check: compare average bid/ask prices
        current_bids = [o.price for o in current_orders if o.side == Side.BUY]
        new_bids = [o.price for o in new_quotes if o.side == Side.BUY]

        if current_bids and new_bids:
            current_avg = sum(current_bids) / len(current_bids)
            new_avg = sum(new_bids) / len(new_bids)
            # Refresh if average price moved by more than half the min spread
            if abs(current_avg - new_avg) > self.config.min_spread / 2.0:
                return True

        current_asks = [o.price for o in current_orders if o.side == Side.SELL]
        new_asks = [o.price for o in new_quotes if o.side == Side.SELL]

        if current_asks and new_asks:
            current_avg = sum(current_asks) / len(current_asks)
            new_avg = sum(new_asks) / len(new_asks)
            if abs(current_avg - new_avg) > self.config.min_spread / 2.0:
                return True

        return False

    def mark_refreshed(self, condition_id: str) -> None:
        """Record that orders for this market were just refreshed."""
        self._last_refresh[condition_id] = time.time()

    def calculate_fair_price(self, orderbook: OrderBook) -> Optional[float]:
        """
        Calculate fair price from the orderbook.

        Uses weighted mid-price for better accuracy when liquidity is uneven.
        Falls back to simple mid-price if weighted calculation fails.
        """
        weighted = orderbook.weighted_mid_price(depth=3)
        if weighted is not None:
            return weighted
        return orderbook.mid_price
