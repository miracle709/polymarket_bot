"""
bot/orderflow.py — Order Flow Confirmation (poly_data)
=======================================================
Reads poly_data's processed/trades.csv every 20 seconds.
Provides two confirmation signals before any trade is placed:

1. Volume pressure — if 60%+ of recent volume (3 min) agrees with
   the trade direction → confirm. If it contradicts → block.

2. Smart wallet tracking — if a tracked wallet entered the same
   direction recently → strong confirm (overrides volume check).

If trades.csv doesn't exist the bot still works normally
(orderflow confirmation is disabled, not blocking).
"""

import os
import csv
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from .logger import setup_logger

logger = setup_logger("orderflow")


# Add wallet addresses of traders you want to track here.
# Format:  'nickname': '0xADDRESS'
SMART_WALLETS: Dict[str, str] = {
    # 'whale1': '0x...',
}


class OrderFlow:

    REFRESH_SEC     = 20
    WINDOW_MINUTES  = 3
    MIN_SMART_USD   = 50.0

    def __init__(self, config):
        self.config = config
        self._trades_path  = Path(config.POLY_DATA_TRADES)
        self._markets_path = Path(config.POLY_DATA_MARKETS)

        self._recent: Dict[str, List[dict]] = defaultdict(list)   # market_id → trades
        self._smart_signals: Dict[str, dict] = {}                  # market_id → signal
        self._token_map: Dict[str, str] = {}                       # token_id  → market_id

        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        if not self._trades_path.exists():
            logger.warning(
                "poly_data trades.csv not found at %s\n"
                "  Order flow confirmation DISABLED — bot will use price signals only.\n"
                "  Run poly_data's update_all.py first to enable this feature.",
                self._trades_path
            )
            return
        self._load_markets()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("OrderFlow started | %s", self._trades_path)

    def stop(self):
        self._running = False

    def is_live(self) -> bool:
        return self._running

    # ── Public API ────────────────────────────────────────────────────────

    def confirm(self, market: dict, direction: str) -> Tuple[bool, str]:
        """
        Returns (allow: bool, reason: str).
        Called by the monitor before placing every order.
        """
        if not self.is_live():
            return True, "orderflow_disabled"

        cid = market.get("condition_id") or market.get("conditionId", "")

        # Smart wallet check (highest priority)
        smart = self._get_smart_signal(cid)
        if smart:
            sw_dir, sw_name, sw_usd = smart
            if sw_dir == direction:
                logger.info("Smart wallet '%s' confirms %s ($%.0f)", sw_name, direction, sw_usd)
                return True, f"smart:{sw_name}"
            else:
                logger.info("Smart wallet '%s' opposes %s — blocking", sw_name, direction)
                return False, f"smart_oppose:{sw_name}"

        # Volume pressure check
        flow = self._get_flow(cid)
        if flow == direction:
            return True, "flow_confirmed"
        if flow is not None:
            logger.info("Flow contradicts %s (flow=%s) — blocking", direction, flow)
            return False, f"flow_contradicts:{flow}"

        # No data → allow (fail-open)
        return True, "flow_neutral"

    def summary(self) -> str:
        with self._lock:
            n_m = len(self._recent)
            n_t = sum(len(v) for v in self._recent.values())
        return f"orderflow: {n_m} markets | {n_t} recent trades"

    # ── Flow calculation ──────────────────────────────────────────────────

    def _get_flow(self, market_id: str) -> Optional[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.WINDOW_MINUTES)
        with self._lock:
            trades = [t for t in self._recent.get(market_id, []) if t["ts"] >= cutoff]

        if len(trades) < 3:
            return None

        up   = sum(t["usd"] for t in trades if t["dir"] == "UP")
        down = sum(t["usd"] for t in trades if t["dir"] == "DOWN")
        total = up + down
        if total < 20:
            return None

        threshold = self.config.FLOW_CONFIRM_THRESHOLD
        if up   / total >= threshold:
            return "UP"
        if down / total >= threshold:
            return "DOWN"
        return None

    def _get_smart_signal(self, market_id: str) -> Optional[Tuple[str, str, float]]:
        if not SMART_WALLETS:
            return None
        with self._lock:
            sig = self._smart_signals.get(market_id)
        if not sig:
            return None
        age = (datetime.now(timezone.utc) - sig["ts"]).total_seconds()
        if age > self.config.SMART_WALLET_MAX_AGE_SECONDS:
            return None
        return sig["dir"], sig["wallet"], sig["usd"]

    # ── Background refresh ────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._read_trades()
                self._prune()
            except Exception as e:
                logger.error("OrderFlow refresh error: %s", e)
            time.sleep(self.REFRESH_SEC)

    def _read_trades(self):
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.WINDOW_MINUTES + 5)
            new: List[dict] = []
            smart_map = {v.lower(): k for k, v in SMART_WALLETS.items()}

            with open(self._trades_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ts = self._parse_ts(row.get("timestamp", ""))
                        if not ts or ts < cutoff:
                            continue

                        market_id    = row.get("market_id", "")
                        taker_dir    = (row.get("taker_direction") or "").upper()
                        nonusdc      = row.get("nonusdc_side", "")
                        usd          = float(row.get("usd_amount") or 0)
                        maker        = (row.get("maker") or "").lower()
                        taker        = (row.get("taker") or "").lower()

                        if not market_id or usd <= 0 or taker_dir not in ("BUY", "SELL"):
                            continue

                        # Map BUY/SELL + token side → UP/DOWN direction
                        is_up_token = "token1" in nonusdc.lower() or nonusdc == "1"
                        if taker_dir == "BUY":
                            direction = "UP" if is_up_token else "DOWN"
                        else:
                            direction = "DOWN" if is_up_token else "UP"

                        trade = {"ts": ts, "dir": direction, "usd": usd,
                                 "maker": maker, "taker": taker}
                        new.append((market_id, trade))

                        # Smart wallet check
                        if usd >= self.MIN_SMART_USD:
                            for addr, nick in [(maker, smart_map.get(maker)),
                                               (taker, smart_map.get(taker))]:
                                if nick:
                                    with self._lock:
                                        self._smart_signals[market_id] = {
                                            "dir": direction, "wallet": nick,
                                            "usd": usd, "ts": ts
                                        }
                                    logger.info("Smart wallet '%s' → %s $%.0f on %s",
                                                nick, direction, usd, market_id[:8])
                    except Exception:
                        continue

            with self._lock:
                for market_id, trade in new:
                    self._recent[market_id].append(trade)

        except FileNotFoundError:
            pass

    def _prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.WINDOW_MINUTES + 5)
        with self._lock:
            for mid in list(self._recent.keys()):
                self._recent[mid] = [t for t in self._recent[mid] if t["ts"] >= cutoff]
                if not self._recent[mid]:
                    del self._recent[mid]

    def _load_markets(self):
        if not self._markets_path.exists():
            return
        try:
            with open(self._markets_path, "r", newline="") as f:
                for row in csv.DictReader(f):
                    cid = row.get("condition_id", "")
                    for key in ("token1", "token2"):
                        tok = row.get(key, "")
                        if cid and tok:
                            self._token_map[tok] = cid
            logger.info("Loaded %d token mappings from markets.csv", len(self._token_map))
        except Exception as e:
            logger.error("Failed to load markets.csv: %s", e)

    @staticmethod
    def _parse_ts(ts_str: str) -> Optional[datetime]:
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            pass
        try:
            dt = datetime.fromtimestamp(float(ts_str), tz=timezone.utc)
            return dt
        except (ValueError, OSError):
            return None
