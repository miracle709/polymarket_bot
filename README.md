# Polymarket bot + poly_data — setup and operation

This guide describes how **`polymarket_bot`** works and how to run it with **`poly_data`** (optional but recommended for order-flow confirmation).

---

## What the bot does

- **Price edge**: Streams **Binance** BTC/ETH spot via WebSocket (falls back to REST if the socket fails). It compares implied probabilities to **Polymarket** prices for short-dated **Up or Down** crypto markets.
- **Strategies** (see `main.py`, `bot/strategy.py`): late-window momentum vs. Polymarket odds, and an overreaction fade after sharp moves.
- **Execution**: Uses Polymarket **CLOB** APIs (`config.py`). **`DRY_RUN=true`** (default) avoids real orders; live trading needs API keys and a private key in `.env`.
- **Optional poly_data**: If `poly_data/processed/trades.csv` exists, `bot/orderflow.py` refreshes it every **20s** and can confirm or block trades using recent volume and optional smart-wallet tracking. If the file is missing, order-flow confirmation stays **off** and the bot still runs on **price signals only** (fail-open).

---

## Repository layout

```
works/
└── polymarket_bot/              ← bot root (run commands from here)
    ├── main.py                  ← asyncio entry: Binance feed + Monitor loop
    ├── config.py                ← settings + env overrides
    ├── requirements.txt         ← pip: aiohttp, numpy, python-dotenv
    ├── run_polydata.py          ← optional loop: `uv run python update_all.py` every 60s
    ├── .env.example
    ├── bot/
    │   ├── monitor.py           ← poll loop, strategy + orderflow + trader
    │   ├── market_fetcher.py    ← Gamma + CLOB: BTC/ETH “UP OR DOWN” markets
    │   ├── orderflow.py         ← reads poly_data trades.csv
    │   ├── binance_feed.py
    │   └── ...
    └── poly_data/               ← data pipeline (uv / `pyproject.toml`)
        ├── update_all.py        ← markets → goldsky → processed trades
        └── processed/
            └── trades.csv       ← created after a successful pipeline run
```

---

## What poly_data does

Runs three stages (see `poly_data/update_all.py`):

1. Fetches markets → `poly_data/markets.csv`
2. Appends order-filled events from Goldsky → `poly_data/goldsky/orderFilled.csv`
3. Processes events → **`poly_data/processed/trades.csv`**

Details and column docs: `polymarket_bot/poly_data/README.md`.

---

## One-time setup

### 1. Bot dependencies (virtualenv recommended)

```bash
cd polymarket_bot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # edit for live trading; DRY_RUN=true is safe default
```

### 2. poly_data: install uv and sync

```bash
cd polymarket_bot/poly_data
uv sync
```

### 3. (Strongly recommended) Seed Goldsky data

First full subgraph scrape can take a long time. Download the snapshot and place it as `poly_data/goldsky/orderFilled.csv` (see URLs in `poly_data/README.md`).

### 4. Run the pipeline once

```bash
cd polymarket_bot/poly_data
uv run python update_all.py
```

Verify (from **`polymarket_bot`**):

```bash
tail -5 poly_data/processed/trades.csv
```

Or, if your shell is already in **`polymarket_bot/poly_data`**:

```bash
tail -5 processed/trades.csv
```

---

## How to run (every day)

Use **two terminals**, both with `cd polymarket_bot` and (if you use it) `source .venv/bin/activate`.

**Terminal 1 — keep trades fresh (optional)**

```bash
python run_polydata.py
```

This runs `update_all.py` inside `poly_data/` about every **60 seconds**. Each invocation has a **120s** subprocess timeout: long runs may print `timeout — will retry` and try again on the next cycle—this is expected if `update_all.py` is slower than the timeout.

**Terminal 2 — bot**

```bash
DRY_RUN=true python main.py
# Live trading (requires valid .env):
# DRY_RUN=false python main.py
```

Startup logs summarize symbols, stake, edge settings, and dry-run mode (`main.py` / `config.py`).

---

## Configuration reference

| Item | Source | Notes |
|------|--------|--------|
| API keys / private key | `.env` | Required when `DRY_RUN=false` |
| `DRY_RUN` | `.env` | Default `true` |
| `EDGE_THRESHOLD`, `OVERREACTION_PCT`, `FLOW_THRESHOLD` | `.env` | Maps to strategy and `FLOW_CONFIRM_THRESHOLD` in `config.py` |
| `POLY_DATA_TRADES`, `POLY_DATA_MARKETS` | `.env` | Defaults: `poly_data/processed/trades.csv`, `poly_data/markets.csv` |
| Symbols, poll interval, risk caps | `config.py` | e.g. `POLL_INTERVAL_SECONDS` (default **10**), `SYMBOLS` default `BTC`, `ETH` |

Smart wallets for order-flow boosts: edit **`SMART_WALLETS`** in `bot/orderflow.py`.

---

## How markets are selected

`bot/market_fetcher.py` loads active crypto-tagged markets from **Gamma**, keeps those whose question contains **`BTC` or `ETH`** and the phrase **`UP OR DOWN`**, then enriches with **CLOB** midpoint prices. If Polymarket changes titles/tags, the bot can log **zero** markets until filters match again.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `ModuleNotFoundError: aiohttp` | Dependencies not installed or venv not activated — `pip install -r requirements.txt` from `polymarket_bot`. |
| `poly_data trades.csv not found` | Pipeline not run or wrong path — run `uv run python update_all.py` in `poly_data/`, or set `POLY_DATA_TRADES`. Bot still runs; order-flow confirmation is off. |
| `tail: .../processed/trades.csv: No such file` | Same as above — file is only created after a successful `update_all.py` (and processing stage). |
| `Scan: 0 markets` | No Gamma markets matched the BTC/ETH + “UP OR DOWN” filter, or CLOB enrichment failed (network/API). |
| `run_polydata.py` → `timeout — will retry` | `update_all.py` exceeded **120s**; pipeline may still be progressing in another terminal if started manually, or consider running `uv run python update_all.py` alone until stable. |

---

## Order-flow behavior (when `trades.csv` is present)

| Situation | Typical behavior |
|-----------|------------------|
| Strong volume agrees with signal | Confirm |
| Volume contradicts | Block |
| Tracked smart wallet agrees | Strong confirm |
| `trades.csv` missing | **Allow** (price-only; reason `orderflow_disabled`) |

---

## Links

- Polymarket API (settings): https://polymarket.com/settings → API  
- poly_data upstream: https://github.com/warproxxx/poly_data  
