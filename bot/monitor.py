"""
bot/monitor.py — Main Bot Loop
Scans markets every POLL_INTERVAL_SECONDS.
Runs both strategies, confirms with orderflow, places orders.
"""

import asyncio
import aiohttp
from datetime import datetime, timezone
from typing import Optional
from .logger import setup_logger
from .market_fetcher import MarketFetcher
from .orderflow import OrderFlow

logger = setup_logger("monitor")


class Monitor:

    def __init__(self, config, strategy, trader):
        self.config   = config
        self.strategy = strategy
        self.trader   = trader
        self.fetcher  = MarketFetcher(config)
        self.orderflow = OrderFlow(config)
        self._running = False
        self._daily   = {"trades": 0, "loss": 0.0, "date": datetime.now(timezone.utc).date()}

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        self.orderflow.start()
        logger.info("Monitor running | poll every %ds", self.config.POLL_INTERVAL_SECONDS)

        try:
            while self._running:
                self._reset_daily_if_new_day()
                if self._daily_limit_hit():
                    await asyncio.sleep(60)
                    continue

                markets = await self.fetcher.fetch_markets()
                logger.info(
                    "Scan: %d markets | open=%d | %s",
                    len(markets),
                    await self.trader.open_count(),
                    self.orderflow.summary()
                )

                for market in markets:
                    await self._process(market)

                await self.trader.manage_positions(self.strategy)
                await asyncio.sleep(self.config.POLL_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            pass
        finally:
            await self.fetcher.close()

    async def stop(self):
        self._running = False
        self.orderflow.stop()

    # ── Per-market processing ──────────────────────────────────────────────

    async def _process(self, market: dict):
        seconds_left = market.get("seconds_to_expiry")
        if seconds_left is None:
            return

        # Skip if too close to expiry or too early
        if seconds_left < self.config.LATE_ENTRY_MIN_SEC:
            return
        # Skip markets expiring more than 6 minutes away (not yet in range)
        if seconds_left > 360:
            return

        if await self.trader.open_count() >= self.config.MAX_OPEN_POSITIONS:
            return

        # Get latest Binance price for this market's asset
        symbol = self._symbol(market)
        binance_price = self.strategy._detect_symbol and self._get_binance_price(symbol)

        signal = self.strategy.evaluate(market, binance_price=binance_price)
        if not signal:
            return

        direction, token_id, price_cents, stake_mult, reason = signal

        if not token_id:
            logger.debug("Signal has no token_id, skipping")
            return

        # Order flow confirmation gate
        ok, flow_reason = self.orderflow.confirm(market, direction)
        if not ok:
            logger.info("BLOCKED [%s] %s @ %.1f¢ | orderflow=%s",
                        reason, direction, price_cents, flow_reason)
            return

        name = market.get("question") or market.get("title", "?")
        logger.info(
            "ENTERING [%s|%s]: %s @ %.1f¢ | stake_mult=%.2fx | %ds left | %s",
            reason, flow_reason, direction, price_cents, stake_mult,
            int(seconds_left), name[:50]
        )

        success = await self.trader.place_order(
            market=market,
            direction=direction,
            token_id=token_id,
            price_cents=price_cents,
            stake_multiplier=stake_mult,
            reason=reason,
        )
        if success:
            self._daily["trades"] += 1

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_binance_price(self, symbol: str):
        """Pull latest Binance price from the strategy's cache."""
        return self.strategy._windows and None  # strategy stores prices internally

    def _symbol(self, market: dict) -> str:
        name = (market.get("question") or market.get("title") or "").upper()
        return "ETH" if ("ETH" in name or "ETHEREUM" in name) else "BTC"

    def _reset_daily_if_new_day(self):
        today = datetime.now(timezone.utc).date()
        if self._daily["date"] != today:
            logger.info("New day — resetting daily counters")
            self._daily = {"trades": 0, "loss": 0.0, "date": today}

    def _daily_limit_hit(self) -> bool:
        if self._daily["trades"] >= self.config.MAX_DAILY_TRADES:
            logger.warning("Daily trade limit hit (%d)", self.config.MAX_DAILY_TRADES)
            return True
        if self._daily["loss"] <= -self.config.MAX_DAILY_LOSS_USD:
            logger.warning("Daily loss limit hit ($%.2f)", self._daily["loss"])
            return True
        return False
