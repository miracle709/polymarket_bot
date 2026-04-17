"""
bot/market_fetcher.py — Polymarket Market Fetcher
Fetches live BTC/ETH 5-minute Up-or-Down markets from Gamma + CLOB APIs.
Enriches each market with current UP/DOWN prices and time to expiry.
"""

import aiohttp
from datetime import datetime, timezone
from typing import List, Optional
from .logger import setup_logger

logger = setup_logger("fetcher")


class MarketFetcher:

    def __init__(self, config):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_markets(self) -> List[dict]:
        """
        Returns list of enriched active BTC/ETH 5-min markets.
        Each market dict has: up_price_cents, down_price_cents,
        up_token_id, down_token_id, condition_id, seconds_to_expiry.
        """
        markets = []
        for symbol in self.config.SYMBOLS:
            try:
                raw = await self._fetch_symbol_markets(symbol)
                for m in raw:
                    enriched = await self._enrich(m)
                    if enriched:
                        markets.append(enriched)
            except Exception as e:
                logger.error("Failed to fetch %s markets: %s", symbol, e)
        return markets

    async def _fetch_symbol_markets(self, symbol: str) -> List[dict]:
        session = self._get_session()
        try:
            async with session.get(
                f"{self.config.GAMMA_API_URL}/markets",
                params={"tag": "crypto", "active": "true", "closed": "false", "limit": 100},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.error("Gamma API %s", resp.status)
                    return []
                data = await resp.json()
                all_markets = data if isinstance(data, list) else data.get("markets", [])

                result = []
                for m in all_markets:
                    name = (m.get("question") or m.get("title") or "").upper()
                    if symbol in name and "UP OR DOWN" in name:
                        result.append(m)
                return result
        except Exception as e:
            logger.error("Gamma fetch error: %s", e)
            return []

    async def _enrich(self, market: dict) -> Optional[dict]:
        """Add prices, token IDs, and expiry seconds to a market."""
        try:
            condition_id = market.get("conditionId") or market.get("condition_id")
            if not condition_id:
                return None

            tokens = market.get("tokens") or market.get("outcomes", [])
            up_token = down_token = None
            for t in tokens:
                outcome = (t.get("outcome") or t.get("name") or "").upper()
                if outcome in ("YES", "UP"):
                    up_token = t.get("token_id") or t.get("tokenId")
                elif outcome in ("NO", "DOWN"):
                    down_token = t.get("token_id") or t.get("tokenId")

            if not up_token:
                return None

            # Fetch midpoint price for UP token
            session = self._get_session()
            async with session.get(
                f"{self.config.CLOB_API_URL}/midpoint",
                params={"token_id": up_token},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                price_data = await resp.json()
                up_price_cents = float(price_data.get("mid", 0.5)) * 100

            market["up_price_cents"]   = up_price_cents
            market["down_price_cents"] = 100.0 - up_price_cents
            market["up_token_id"]      = up_token
            market["down_token_id"]    = down_token
            market["condition_id"]     = condition_id

            # Parse expiry
            end_raw = (market.get("endDateIso") or market.get("end_date_iso")
                       or market.get("endDate"))
            if end_raw:
                try:
                    if isinstance(end_raw, (int, float)):
                        end_dt = datetime.fromtimestamp(end_raw, tz=timezone.utc)
                    else:
                        end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    market["seconds_to_expiry"] = (end_dt - datetime.now(timezone.utc)).total_seconds()
                except Exception:
                    market["seconds_to_expiry"] = None

            return market

        except Exception as e:
            logger.debug("Enrich error: %s", e)
            return None
