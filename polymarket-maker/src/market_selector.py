"""Market selection and ranking for the market maker."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.strategy import Market

logger = logging.getLogger(__name__)

# Price bounds: avoid near-certain outcomes where market making is risky
MIN_PRICE_THRESHOLD = 0.10
MAX_PRICE_THRESHOLD = 0.90


class MarketSelector:
    """Selects and ranks markets suitable for market making."""

    def __init__(
        self,
        min_volume: float = 1000.0,
        min_liquidity: float = 5000.0,
        max_end_days: int = 30,
        blacklist: Optional[list[str]] = None,
        whitelist: Optional[list[str]] = None,
    ):
        self.min_volume: float = min_volume
        self.min_liquidity: float = min_liquidity
        self.max_end_days: int = max_end_days
        self.blacklist: set[str] = set(blacklist or [])
        self.whitelist: set[str] = set(whitelist or [])

    @classmethod
    def from_config(cls, config: dict) -> MarketSelector:
        """Create a MarketSelector from a config dict (e.g. loaded from markets.json)."""
        return cls(
            min_volume=config.get("min_volume_24h", 1000.0),
            min_liquidity=config.get("min_liquidity", 5000.0),
            max_end_days=config.get("max_end_days", 30),
            blacklist=config.get("blacklist", []),
            whitelist=config.get("whitelist", []),
        )

    def filter_markets(
        self,
        markets: list[Market],
        current_prices: Optional[dict[str, float]] = None,
    ) -> list[Market]:
        """
        Select markets suitable for market making.

        Filters:
        - Not in blacklist (or explicitly in whitelist)
        - Not flagged/disputed
        - Still active
        - Daily volume > min_volume
        - Existing liquidity > min_liquidity
        - Not expiring within max_end_days
        - Price not too extreme (between 0.10 and 0.90)

        Args:
            markets: List of candidate markets.
            current_prices: Optional dict of condition_id -> mid-price for price filtering.
        """
        now = datetime.now(timezone.utc)
        filtered: list[Market] = []

        for market in markets:
            # Whitelist overrides all other checks (except flagged)
            whitelisted = market.condition_id in self.whitelist

            # Always skip flagged markets
            if market.flagged:
                logger.debug("Skipping flagged market: %s", market.condition_id[:12])
                continue

            # Always skip inactive markets
            if not market.active:
                logger.debug("Skipping inactive market: %s", market.condition_id[:12])
                continue

            # Blacklist check
            if market.condition_id in self.blacklist:
                logger.debug("Skipping blacklisted market: %s", market.condition_id[:12])
                continue

            if not whitelisted:
                # Volume check
                if market.volume_24h < self.min_volume:
                    logger.debug(
                        "Skipping low-volume market: %s (vol=%.0f)",
                        market.condition_id[:12], market.volume_24h,
                    )
                    continue

                # Liquidity check
                if market.liquidity < self.min_liquidity:
                    logger.debug(
                        "Skipping low-liquidity market: %s (liq=%.0f)",
                        market.condition_id[:12], market.liquidity,
                    )
                    continue

                # End date check: must not expire too soon
                days_until_end = (market.end_date - now).total_seconds() / 86400.0
                if days_until_end < self.max_end_days:
                    logger.debug(
                        "Skipping near-expiry market: %s (%.1f days left)",
                        market.condition_id[:12], days_until_end,
                    )
                    continue

                # Price extremity check
                if current_prices and market.condition_id in current_prices:
                    price = current_prices[market.condition_id]
                    if price < MIN_PRICE_THRESHOLD or price > MAX_PRICE_THRESHOLD:
                        logger.debug(
                            "Skipping extreme-priced market: %s (price=%.2f)",
                            market.condition_id[:12], price,
                        )
                        continue

            filtered.append(market)

        logger.info("Filtered %d / %d markets", len(filtered), len(markets))
        return filtered

    def rank_markets(self, markets: list[Market], spreads: Optional[dict[str, float]] = None) -> list[Market]:
        """
        Rank markets by profitability potential.

        Score = volume_weight * spread_weight
        - Higher volume = more fill opportunities
        - Wider spread = more profit per trade

        Markets with wider spreads and higher volume rank higher.

        Args:
            markets: Pre-filtered list of markets.
            spreads: Optional dict of condition_id -> current spread.
        """
        def score(market: Market) -> float:
            # Volume component: log-scale to avoid mega-volume markets dominating
            volume_score = market.volume_24h

            # Spread component: wider spread = more profit per trade
            spread_score = 1.0
            if spreads and market.condition_id in spreads:
                spread_val = spreads[market.condition_id]
                # Wider spread up to a point is good (more profit), but extremely
                # wide spreads may indicate illiquid/risky markets
                if 0.01 <= spread_val <= 0.15:
                    spread_score = spread_val * 10.0  # Normalize: 0.03 -> 0.3, 0.10 -> 1.0
                elif spread_val > 0.15:
                    spread_score = 0.5  # Penalize very wide spreads (illiquid/risky)

            # Liquidity acts as a confidence factor
            liquidity_factor = min(market.liquidity / 10000.0, 2.0)  # Cap at 2x

            return volume_score * spread_score * liquidity_factor

        ranked = sorted(markets, key=score, reverse=True)
        return ranked
