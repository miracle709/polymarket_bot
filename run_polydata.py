"""
run_polydata.py — Keep poly_data trades fresh (optional)
Run in a second terminal to feed live order flow data into the bot.

Usage:
    cd polybot_v3
    python run_polydata.py
"""

import time
import sys
import subprocess
from pathlib import Path
from datetime import datetime

POLY_DIR = Path("poly_data")
INTERVAL = 60   # update every 60 seconds


def run():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] Updating poly_data... ", end="", flush=True)
    try:
        use_uv = subprocess.run(["which", "uv"], capture_output=True).returncode == 0
        cmd = ["uv", "run", "python", "update_all.py"] if use_uv else ["python", "update_all.py"]
        result = subprocess.run(cmd, cwd=POLY_DIR, capture_output=True, text=True, timeout=120)
        print("done" if result.returncode == 0 else f"exit {result.returncode}")
    except subprocess.TimeoutExpired:
        print("timeout — will retry")
    except Exception as e:
        print(f"error: {e}")


if __name__ == "__main__":
    if not POLY_DIR.exists():
        print("poly_data/ not found. Clone it first:\n  git clone https://github.com/warproxxx/poly_data.git poly_data")
        sys.exit(1)

    print(f"poly_data updater — refreshing every {INTERVAL}s | Ctrl+C to stop")
    while True:
        try:
            run()
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
