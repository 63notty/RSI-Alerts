"""
RSI Discord Alert Bot — TradingView Data Edition
--------------------------------------------------
Pulls RSI directly from TradingView's own servers (via the tradingview-ta
library), so values match exactly what you'd see on tradingview.com.

Checks both 5-minute and 1-hour RSI (14 period) for each symbol below,
and sends a Discord alert ONLY when RSI newly crosses below 30 (oversold)
or above 70 (overbought) — not on every single check — so you don't get
spammed while it sits there.

You should NOT need to understand this code. Just edit WATCHLIST below
if you want to add/remove symbols. Everything else can stay as-is.
"""

import os
import json
import requests
from datetime import datetime, timezone
from tradingview_ta import TA_Handler, Interval

# ======================= SETTINGS =======================

# Discord webhook URL — comes from a GitHub Secret (see setup guide), not hardcoded here.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

OVERSOLD = 30
OVERBOUGHT = 70

# File used to remember the last state between runs, so we only alert on
# a NEW crossing, not every time the script runs.
STATE_FILE = "state.json"

# Every RSI value checked gets appended here with a timestamp, so if an
# alert seems to go missing later, we can look back and see exactly what
# the data showed at that moment. Keeps only the most recent MAX_LOG_LINES
# entries so the file doesn't grow forever.
LOG_FILE = "rsi_log.csv"
MAX_LOG_LINES = 5000

# Each entry: display name, TradingView symbol, exchange, and screener type.
# screener is one of: "crypto", "forex", "cfd" (indices/metals via forex brokers), "america"
WATCHLIST = [
    {"name": "BTC/USD",   "symbol": "BTCUSDT",    "exchange": "BINANCE", "screener": "crypto"},
    {"name": "ETH/USD",   "symbol": "ETHUSDT",    "exchange": "BINANCE", "screener": "crypto"},
    {"name": "US OIL",    "symbol": "USOIL",      "exchange": "TVC",     "screener": "america"},
    {"name": "NASDAQ 100","symbol": "NDX",        "exchange": "TVC",     "screener": "america"},
    {"name": "US 500",    "symbol": "SPX",        "exchange": "TVC",     "screener": "america"},
    {"name": "XAU/USD",   "symbol": "XAUUSD",     "exchange": "OANDA",   "screener": "cfd"},
    {"name": "XAG/USD",   "symbol": "SILVER",     "exchange": "TVC",     "screener": "america"},
    {"name": "EUR/USD",   "symbol": "EURUSD",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "GBP/USD",   "symbol": "GBPUSD",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "USD/JPY",   "symbol": "USDJPY",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "USD/CHF",   "symbol": "USDCHF",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "AUD/USD",   "symbol": "AUDUSD",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "USD/CAD",   "symbol": "USDCAD",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "UK 100",    "symbol": "UK100GBP",   "exchange": "OANDA",   "screener": "cfd"},
]

TIMEFRAMES = [
    {"label": "5m", "interval": Interval.INTERVAL_5_MINUTES},
    {"label": "1h", "interval": Interval.INTERVAL_1_HOUR},
]

# ===========================================================================


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
    # keep only the most recent MAX_LOG_LINES entries so this doesn't grow forever
    lines = lines[-MAX_LOG_LINES:]

    with open(LOG_FILE, "w") as f:
        f.writelines(lines)


def get_status(rsi):
    if rsi <= OVERSOLD:
        return "oversold"
    elif rsi >= OVERBOUGHT:
        return "overbought"
    return "neutral"


def check_symbol(entry, timeframe, state):
    key = f"{entry['symbol']}_{timeframe['label']}"
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

        new_status = get_status(rsi)
        old_status = state.get(key, "neutral")

        log_rsi(entry["name"], timeframe["label"], rsi, new_status)

        # Only alert when we NEWLY enter oversold/overbought territory
        if new_status != old_status and new_status != "neutral":
            emoji = "📉" if new_status == "oversold" else "📈"
            send_discord_alert(
                f"{emoji} **{entry['name']}** ({timeframe['label']}) RSI is **{rsi}** "
                f"— {new_status.upper()}"
            )

        state[key] = new_status

    except Exception as e:
        print(f"Error checking {entry['name']} [{timeframe['label']}]: {e}")


def main():
    state = load_state()
    for entry in WATCHLIST:
        for timeframe in TIMEFRAMES:
            check_symbol(entry, timeframe, state)
    save_state(state)


if __name__ == "__main__":
    main()
