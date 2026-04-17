"""
Polymarket BTC/ETH Bot — Superior Strategy
===========================================
Runs two proven edges:
  1. Late-window momentum arbitrage (enters last 15–65s per window)
  2. Overreaction fade (bets mean-reversion after 0.35%+ spike)

Both require live Binance price data — the bot connects via WebSocket
(falls back to REST polling if WebSocket is blocked).

Usage:
    DRY_RUN=true  python main.py   # paper trading (safe, default)
    DRY_RUN=false python main.py   # live trading (needs API keys in .env)
"""

import asyncio
import signal
import sys

from bot.monitor import Monitor
from bot.trader import Trader
from bot.strategy import Strategy
from bot.binance_feed import create_feed
from bot.logger import setup_logger
from config import Config

logger = setup_logger("main")


async def main():
    config = Config()
    config.validate()

    logger.info("=" * 62)
    logger.info("  Polymarket Bot  |  Superior Strategy")
    logger.info("=" * 62)
    logger.info("  Symbols        : %s",    ", ".join(config.SYMBOLS))
    logger.info("  Base stake     : $%.2f", config.STAKE_USD)
    logger.info("  Edge threshold : %.1f%%", config.EDGE_THRESHOLD_PCT)
    logger.info("  Overreaction   : %.2f%% in 60s", config.OVERREACTION_PCT)
    logger.info("  Late window    : last 15–65s of each 5-min window")
    logger.info("  Daily limits   : %d trades | $%.0f max loss",
                config.MAX_DAILY_TRADES, config.MAX_DAILY_LOSS_USD)
    logger.info("  Dry run        : %s", config.DRY_RUN)
    logger.info("=" * 62)

    strategy = Strategy(config)
    trader   = Trader(config)

    # ── Binance live price feed ────────────────────────────────────────────
    def on_price(symbol: str, price: float):
        """Routes Binance ticks into the strategy."""
        strategy.update_price(symbol, price)

    binance_feed = await create_feed(on_price)
    logger.info("Binance feed active")

    # ── Main monitor ───────────────────────────────────────────────────────
    monitor = Monitor(config, strategy, trader)

    # Graceful shutdown on Ctrl+C / SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(_shutdown(monitor, binance_feed))
        )

    await monitor.run()


async def _shutdown(monitor, binance_feed):
    logger.info("Shutting down gracefully...")
    await monitor.stop()
    await binance_feed.stop()
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
