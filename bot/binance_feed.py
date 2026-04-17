"""
bot/binance_feed.py — Real-time Binance Price Feed
====================================================
Streams live BTC/ETH trade ticks via Binance WebSocket.
This is the core data advantage of the new strategy:
  Polymarket odds lag Binance spot by 10–30 seconds.
  We read Binance → compute true probability → compare to Polymarket odds.

Falls back to Binance REST API (polling every 2s) if WebSocket fails.
"""

import asyncio
import json
import time
import aiohttp
from typing import Callable, Dict, Optional
from .logger import setup_logger

logger = setup_logger("binance")

# Binance WebSocket combined stream endpoint
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade/ethusdt@trade"
BINANCE_REST_URL = "https://api.binance.com/api/v3/ticker/price"

PAIR_TO_SYMBOL = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
}


class BinanceFeed:
    """
    Connects to Binance WebSocket and streams real-time prices.
    Calls on_price(symbol, price) on every trade tick.
    Auto-reconnects on disconnect.
    """

    def __init__(self, on_price: Callable[[str, float], None]):
        self.on_price = on_price
        self._running = False
        self._prices: Dict[str, dict] = {}   # symbol → {price, ts}
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        # Give WebSocket a moment to connect before bot starts polling
        await asyncio.sleep(1.5)
        logger.info("Binance feed started (BTC + ETH live ticks)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    def get_price(self, symbol: str) -> Optional[float]:
        """Return latest price if fresh (< 5s old), else None."""
        entry = self._prices.get(symbol)
        if not entry:
            return None
        if time.time() - entry["ts"] > 5:
            return None
        return entry["price"]

    async def _run_loop(self):
        """Outer reconnect loop — keeps feed alive through network issues."""
        backoff = 1
        while self._running:
            try:
                await self._connect_ws()
                backoff = 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Binance WS error: %s | retry in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _connect_ws(self):
        """Open WebSocket and stream ticks."""
        session = aiohttp.ClientSession()
        try:
            async with session.ws_connect(
                BINANCE_WS_URL,
                heartbeat=20,
                timeout=aiohttp.ClientWSTimeout(ws_close=10)
            ) as ws:
                logger.info("Binance WS connected")
                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle_tick(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning("Binance WS closed (%s)", msg.type)
                        break
        finally:
            await session.close()

    def _handle_tick(self, raw: str):
        try:
            data = json.loads(raw)
            if data.get("e") != "trade":
                return
            pair   = data.get("s", "").upper()
            price  = float(data.get("p", 0))
            symbol = PAIR_TO_SYMBOL.get(pair)
            if symbol and price > 0:
                self._prices[symbol] = {"price": price, "ts": time.time()}
                self.on_price(symbol, price)
        except Exception:
            pass


class BinanceFeedREST:
    """
    Fallback: polls Binance REST API every 2 seconds.
    Used when WebSocket is blocked (firewall, VPN, etc).
    Less real-time but still useful for the edge calculation.
    """

    def __init__(self, on_price: Callable[[str, float], None]):
        self.on_price = on_price
        self._running = False
        self._prices: Dict[str, dict] = {}
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        await asyncio.sleep(1)
        logger.warning("Using Binance REST fallback (WebSocket unavailable) — 2s delay")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    def get_price(self, symbol: str) -> Optional[float]:
        entry = self._prices.get(symbol)
        if not entry:
            return None
        if time.time() - entry["ts"] > 10:
            return None
        return entry["price"]

    async def _poll_loop(self):
        session = aiohttp.ClientSession()
        pairs = ["BTCUSDT", "ETHUSDT"]
        try:
            while self._running:
                for pair in pairs:
                    try:
                        async with session.get(
                            BINANCE_REST_URL,
                            params={"symbol": pair},
                            timeout=aiohttp.ClientTimeout(total=3)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                price = float(data.get("price", 0))
                                symbol = PAIR_TO_SYMBOL.get(pair)
                                if symbol and price > 0:
                                    self._prices[symbol] = {"price": price, "ts": time.time()}
                                    self.on_price(symbol, price)
                    except Exception as e:
                        logger.debug("REST price fetch failed for %s: %s", pair, e)
                await asyncio.sleep(2)
        finally:
            await session.close()


async def create_feed(on_price: Callable[[str, float], None]):
    """
    Factory: tries WebSocket first, falls back to REST automatically.
    Returns the started feed object.
    """
    feed = BinanceFeed(on_price=on_price)
    try:
        await asyncio.wait_for(feed.start(), timeout=5)
        return feed
    except Exception as e:
        logger.warning("WebSocket failed (%s), switching to REST fallback", e)
        rest_feed = BinanceFeedREST(on_price=on_price)
        await rest_feed.start()
        return rest_feed
