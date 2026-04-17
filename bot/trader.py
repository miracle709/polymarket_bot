"""
bot/trader.py — Order Execution & Position Management
Places orders, tracks open positions, closes on TP/SL.
In DRY_RUN mode everything is simulated with no real funds touched.
"""

import time
import json
import hmac
import hashlib
import base64
import aiohttp
from datetime import datetime, timezone
from typing import Dict, List, Optional
from .logger import setup_logger

logger = setup_logger("trader")


class Trader:

    def __init__(self, config):
        self.config = config
        self._positions: Dict[str, dict] = {}   # token_id → position
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Session ────────────────────────────────────────────────────────────

    def _session_(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # ── Auth ───────────────────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        ts  = str(int(time.time()))
        msg = ts + method.upper() + path + body
        sig = hmac.new(
            self.config.API_SECRET.encode(),
            msg.encode(),
            hashlib.sha256
        ).digest()
        return {
            "POLY-API-KEY":    self.config.API_KEY,
            "POLY-SIGNATURE":  base64.b64encode(sig).decode(),
            "POLY-TIMESTAMP":  ts,
            "POLY-PASSPHRASE": self.config.API_PASSPHRASE,
            "Content-Type":    "application/json",
        }

    # ── Placing orders ─────────────────────────────────────────────────────

    async def place_order(
        self,
        market: dict,
        direction: str,
        token_id: str,
        price_cents: float,
        stake_multiplier: float = 1.0,
        reason: str = "",
    ) -> bool:
        """Place a buy order. Returns True on success."""

        stake = round(self.config.STAKE_USD * stake_multiplier, 2)
        price_dec = price_cents / 100.0
        size  = round(stake / max(price_dec, 0.01), 2)
        name  = market.get("question") or market.get("title", "?")

        if self.config.DRY_RUN:
            logger.info(
                "[DRY RUN] BUY %s | %.1f¢ | size=%.2f | $%.2f | %s | %s",
                direction, price_cents, size, stake, reason, name[:50]
            )
            self._positions[token_id] = {
                "direction":          direction,
                "token_id":           token_id,
                "market_name":        name,
                "entry_price_cents":  price_cents,
                "current_price_cents":price_cents,
                "size":               size,
                "cost_usd":           stake,
                "reason":             reason,
                "entered_at":         datetime.now(timezone.utc).isoformat(),
                "condition_id":       market.get("condition_id"),
            }
            return True

        # ── Live order ────────────────────────────────────────────────────
        try:
            payload = {
                "orderType": "GTC",
                "tokenID":   token_id,
                "price":     round(price_dec, 4),
                "size":      size,
                "side":      "BUY",
                "feeRateBps":"0",
                "nonce":     str(int(time.time() * 1000)),
            }
            body    = json.dumps(payload)
            path    = "/order"
            headers = self._sign("POST", path, body)

            async with self._session_().post(
                f"{self.config.CLOB_API_URL}{path}",
                headers=headers,
                data=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if resp.status == 200 and result.get("success"):
                    order_id = result.get("orderID", "")
                    logger.info(
                        "ORDER PLACED %s | %.1f¢ | size=%.2f | id=%s | %s",
                        direction, price_cents, size, order_id, reason
                    )
                    self._positions[token_id] = {
                        "direction":           direction,
                        "token_id":            token_id,
                        "market_name":         name,
                        "entry_price_cents":   price_cents,
                        "current_price_cents": price_cents,
                        "size":                size,
                        "cost_usd":            stake,
                        "reason":              reason,
                        "entered_at":          datetime.now(timezone.utc).isoformat(),
                        "order_id":            order_id,
                        "condition_id":        market.get("condition_id"),
                    }
                    return True
                else:
                    logger.error("Order rejected: %s", result)
                    return False

        except Exception as e:
            logger.error("Exception placing order: %s", e)
            return False

    # ── Position management ─────────────────────────────────────────────────

    async def open_count(self) -> int:
        return len(self._positions)

    async def manage_positions(self, strategy):
        """Check all open positions for TP/SL and close if triggered."""
        if not self._positions:
            return

        to_close = []
        for token_id, pos in self._positions.items():
            price = await self._fetch_price(token_id)
            if price is not None:
                pos["current_price_cents"] = price

            entry   = pos["entry_price_cents"]
            current = pos["current_price_cents"]
            direction = pos["direction"]
            pnl_pct = ((current - entry) / entry * 100) if direction == "UP" else \
                      ((entry - current) / entry * 100)
            logger.debug(
                "Pos %s | entry=%.1f¢ now=%.1f¢ PnL=%.1f%% | %s",
                direction, entry, current, pnl_pct, pos["market_name"][:40]
            )

            if strategy.should_take_profit(pos) or strategy.should_stop_loss(pos):
                to_close.append(token_id)

        for token_id in to_close:
            await self._close(token_id)

    async def _fetch_price(self, token_id: str) -> Optional[float]:
        try:
            async with self._session_().get(
                f"{self.config.CLOB_API_URL}/midpoint",
                params={"token_id": token_id},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("mid", 0)) * 100
        except Exception as e:
            logger.debug("Price fetch failed %s: %s", token_id[:8], e)
        return None

    async def _close(self, token_id: str):
        pos = self._positions.get(token_id)
        if not pos:
            return

        direction = pos["direction"]
        entry     = pos["entry_price_cents"]
        current   = pos["current_price_cents"]
        size      = pos["size"]
        pnl       = (current - entry) * size / 100 if direction == "UP" else \
                    (entry - current) * size / 100

        if self.config.DRY_RUN:
            logger.info(
                "[DRY RUN] CLOSE %s | exit=%.1f¢ | P&L=$%.2f | %s",
                direction, current, pnl, pos["market_name"][:40]
            )
            del self._positions[token_id]
            return

        try:
            payload = {
                "orderType": "FOK",
                "tokenID":   token_id,
                "price":     round((100.0 - current) / 100.0, 4),
                "size":      size,
                "side":      "SELL",
                "feeRateBps":"0",
                "nonce":     str(int(time.time() * 1000)),
            }
            body    = json.dumps(payload)
            path    = "/order"
            headers = self._sign("POST", path, body)
            async with self._session_().post(
                f"{self.config.CLOB_API_URL}{path}",
                headers=headers,
                data=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if resp.status == 200:
                    logger.info("CLOSED %s | P&L=$%.2f | %s", direction, pnl, pos["market_name"][:40])
                else:
                    logger.error("Close failed: %s", result)
        except Exception as e:
            logger.error("Exception closing: %s", e)
        finally:
            self._positions.pop(token_id, None)

    def positions_summary(self) -> List[dict]:
        return list(self._positions.values())
