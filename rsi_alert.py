"""
RSI Discord Alert Bot — Broker-Matched Edition
--------------------------------------------------
Pulls RSI directly from TradingView's own servers, using the SAME broker
feed for each instrument that you actually see on your own TradingView
charts (confirmed by checking each symbol's exchange directly). This
should make every alert match your chart far more closely than using a
generic third-party source like Yahoo Finance.

All symbols are checked AT THE SAME TIME (in parallel) instead of one after
another, so a full run finishes quickly and doesn't pile up against the
next scheduled trigger.

Sends a Discord alert ONLY when RSI newly crosses below 30 (oversold) or
above 70 (overbought) — not on every single check — so you don't get
spammed while it sits there.

You should NOT need to understand this code. Just edit WATCHLIST below
if you want to add/remove symbols. Everything else can stay as-is.
"""

import os
import json
import threading
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from tradingview_ta import TA_Handler, Interval

# ======================= SETTINGS =======================

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

OVERSOLD = 30
OVERBOUGHT = 70

STATE_FILE = "state.json"
LOG_FILE = "rsi_log.csv"
MAX_LOG_LINES = 5000

MAX_WORKERS = 10

# Each entry: display name, TradingView symbol, exchange (matched to YOUR
# actual chart's data provider), and screener type.
WATCHLIST = [
    {"name": "BTC/USD",    "symbol": "BTCUSDT", "exchange": "BINANCE",    "screener": "crypto"},
    {"name": "ETH/USD",    "symbol": "ETHUSDT", "exchange": "BINANCE",    "screener": "crypto"},
    {"name": "XAU/USD",    "symbol": "XAUUSD",  "exchange": "OANDA",      "screener": "cfd"},
    {"name": "XAG/USD",    "symbol": "SILVER",  "exchange": "CAPITALCOM","screener": "cfd"},
    {"name": "EUR/USD",    "symbol": "EURUSD",  "exchange": "FXCM",       "screener": "forex"},
    {"name": "GBP/USD",    "symbol": "GBPUSD",  "exchange": "FXCM",       "screener": "forex"},
    {"name": "USD/JPY",    "symbol": "USDJPY",  "exchange": "FXCM",       "screener": "forex"},
    {"name": "USD/CHF",    "symbol": "USDCHF",  "exchange": "FXCM",       "screener": "forex"},
    {"name": "AUD/USD",    "symbol": "AUDUSD",  "exchange": "FOREXCOM",   "screener": "forex"},
    {"name": "USD/CAD",    "symbol": "USDCAD",  "exchange": "OANDA",      "screener": "forex"},
    {"name": "US OIL",     "symbol": "USOIL",   "exchange": "TVC",        "screener": "cfd"},
    {"name": "NASDAQ 100", "symbol": "USTEC",   "exchange": "ICMARKETS", "screener": "cfd"},
    {"name": "US 500",     "symbol": "US500",   "exchange": "PEPPERSTONE","screener": "cfd"},
    {"name": "UK 100",     "symbol": "UK100",   "exchange": "ACTIVTRADES","screener": "cfd"},
]

TIMEFRAMES = [
    {"label": "5m", "interval": Interval.INTERVAL_5_MINUTES},
    {"label": "1h", "interval": Interval.INTERVAL_1_HOUR},
]

# ===========================================================================

_state_lock = threading.Lock()


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        print("No DISCORD_WEBHOOK_URL set, skipping send. Message was:")
        print(message)
        return
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
        if response.status_code not in (200, 204):
            print(f"Discord error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")


def log_rsi(name, timeframe_label, rsi, status):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"{timestamp},{name},{timeframe_label},{rsi},{status}\n"

    lines = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()

    lines.append(line)
    lines = lines[-MAX_LOG_LINES:]

    with open(LOG_FILE, "w") as f:
        f.writelines(lines)


def get_status(rsi):
    if rsi <= OVERSOLD:
        return "oversold"
    elif rsi >= OVERBOUGHT:
        return "overbought"
    return "neutral"


def handle_result(key, name, timeframe_label, rsi, state):
    with _state_lock:
        new_status = get_status(rsi)
        old_status = state.get(key, "neutral")

        log_rsi(name, timeframe_label, rsi, new_status)

        if new_status != old_status and new_status != "neutral":
            emoji = "📉" if new_status == "oversold" else "📈"
            send_discord_alert(
                f"{emoji} **{name}** ({timeframe_label}) RSI is **{rsi}** — {new_status.upper()}"
            )

        state[key] = new_status


def check_symbol(entry, timeframe, state):
    key = f"{entry['symbol']}_{entry['exchange']}_{timeframe['label']}"
    try:
        handler = TA_Handler(
            symbol=entry["symbol"],
            exchange=entry["exchange"],
            screener=entry["screener"],
            interval=timeframe["interval"],
        )
        analysis = handler.get_analysis()
        rsi = round(analysis.indicators["RSI"], 2)
        print(f"{entry['name']} [{timeframe['label']}]: RSI = {rsi}")
        handle_result(key, entry["name"], timeframe["label"], rsi, state)

    except Exception as e:
        print(f"Error checking {entry['name']} [{timeframe['label']}]: {e}")


def main():
    state = load_state()

    tasks = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for entry in WATCHLIST:
            for timeframe in TIMEFRAMES:
                tasks.append(executor.submit(check_symbol, entry, timeframe, state))

        for task in as_completed(tasks):
            task.result()

    save_state(state)


if __name__ == "__main__":
    main()
