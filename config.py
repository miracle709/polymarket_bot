"""
config.py — All bot settings
Edit values here or set via environment variables.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:

    # ── Polymarket API credentials ─────────────────────────────────────────
    # Get from: https://polymarket.com/settings → API
    API_KEY: str        = os.getenv("POLY_API_KEY",        "YOUR_API_KEY_HERE")
    API_SECRET: str     = os.getenv("POLY_API_SECRET",     "YOUR_API_SECRET_HERE")
    API_PASSPHRASE: str = os.getenv("POLY_API_PASSPHRASE", "YOUR_PASSPHRASE_HERE")
    PRIVATE_KEY: str    = os.getenv("POLY_PRIVATE_KEY",    "YOUR_PRIVATE_KEY_HERE")

    # ── Symbols to trade ───────────────────────────────────────────────────
    SYMBOLS: List[str] = field(default_factory=lambda: ["BTC", "ETH"])

    # ── Strategy: Late-window momentum arbitrage ───────────────────────────
    # Only enter when seconds_left is in this range
    LATE_ENTRY_MIN_SEC: int   = 15    # don't enter with less than 15s left
    LATE_ENTRY_MAX_SEC: int   = 65    # only enter in final 65s of window

    # Minimum edge (%) between Binance-implied prob and Polymarket price
    EDGE_THRESHOLD_PCT: float = float(os.getenv("EDGE_THRESHOLD", "7.0"))

    # Brownian motion volatility (per second) — calibrated to BTC/ETH 5-min data
    BTC_VOL_PER_SEC: float = 0.00018   # ~0.8% per 5 min
    ETH_VOL_PER_SEC: float = 0.00025   # ~1.1% per 5 min
    MONTE_CARLO_PATHS: int = 1000      # simulation paths (higher = slower but more accurate)

    # ── Strategy: Overreaction fade ────────────────────────────────────────
    # Enter fade between 60–120s elapsed (after initial spike is confirmed)
    FADE_EVAL_MIN_SEC: int  = 60   # seconds elapsed before checking for overreaction
    FADE_EVAL_MAX_SEC: int  = 120  # stop evaluating after this

    # % price move in first 60s that qualifies as overreaction
    OVERREACTION_PCT: float = float(os.getenv("OVERREACTION_PCT", "0.35"))

    # Only fade if the opposite side is still cheap enough (¢)
    FADE_MAX_ENTRY_CENTS: float = 65.0

    # ── Take profit / stop loss ────────────────────────────────────────────
    TAKE_PROFIT_CENTS: float = 75.0   # exit when position reaches this price
    STOP_LOSS_PCT: float     = 0.60   # exit if position loses 60% of entry value

    # ── Risk management ────────────────────────────────────────────────────
    STAKE_USD: float           = 10.0   # base USD per trade (Kelly adjusts this)
    MAX_OPEN_POSITIONS: int    = 5      # max simultaneous open positions
    MAX_DAILY_LOSS_USD: float  = 100.0  # halt if daily loss exceeds this
    MAX_DAILY_TRADES: int      = 40     # max trades per day
    MIN_MARKET_VOLUME_USD: float = 2000.0  # skip markets thinner than this

    # ── Timing ─────────────────────────────────────────────────────────────
    POLL_INTERVAL_SECONDS: int = 10    # how often to scan for markets

    # ── poly_data integration ──────────────────────────────────────────────
    # Path to poly_data's trades.csv (relative to this file)
    POLY_DATA_TRADES: str = os.getenv("POLY_DATA_TRADES", "poly_data/processed/trades.csv")
    POLY_DATA_MARKETS: str = os.getenv("POLY_DATA_MARKETS", "poly_data/markets.csv")
    FLOW_CONFIRM_THRESHOLD: float = float(os.getenv("FLOW_THRESHOLD", "0.60"))
    SMART_WALLET_MAX_AGE_SECONDS: int = 120

    # ── Safety ─────────────────────────────────────────────────────────────
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() != "false"

    # ── Endpoints ──────────────────────────────────────────────────────────
    CLOB_API_URL: str  = "https://clob.polymarket.com"
    GAMMA_API_URL: str = "https://gamma-api.polymarket.com"

    def validate(self):
        if not self.DRY_RUN:
            missing = [
                name for name, val in [
                    ("POLY_API_KEY",        self.API_KEY),
                    ("POLY_API_SECRET",     self.API_SECRET),
                    ("POLY_API_PASSPHRASE", self.API_PASSPHRASE),
                    ("POLY_PRIVATE_KEY",    self.PRIVATE_KEY),
                ] if "YOUR_" in val
            ]
            if missing:
                raise ValueError(
                    f"Missing credentials for live trading: {missing}\n"
                    "Set DRY_RUN=true to paper-trade without credentials."
                )
        assert 0 < self.STAKE_USD <= 1000,      "STAKE_USD must be 1–1000"
        assert 0 < self.MAX_OPEN_POSITIONS <= 20
        assert 0 < self.EDGE_THRESHOLD_PCT <= 50
