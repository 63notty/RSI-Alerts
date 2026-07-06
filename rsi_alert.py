"""
RSI Discord Alert Bot — Hybrid Data Edition
--------------------------------------------------
Uses TWO data sources:

1. TradingView (via the tradingview-ta library) for crypto, forex, and gold —
   these values come straight from TradingView's own servers, matching
   what you'd see on tradingview.com exactly.

2. Yahoo Finance for real stock indices and commodities (Nasdaq 100, S&P 500,
   US Oil, Silver, UK 100) — TradingView's own API has a hard limitation
   where it does NOT support pure index-type instruments at all, so for
   these we pull price history directly and calculate RSI ourselves using
   Wilder's smoothing method — the same standard formula TradingView uses
   internally — so the numbers stay very close to what you'd see on your
   own chart.

Checks both 5-minute and 1-hour RSI (14 period) for each symbol below,
and sends a Discord alert ONLY when RSI newly crosses below 30 (oversold)
or above 70 (overbought) — not on every single check — so you don't get
spammed while it sits there.

You should NOT need to understand this code. Just edit the two watchlists
below if you want to add/remove symbols. Everything else can stay as-is.
"""

import os
import json
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from tradingview_ta import TA_Handler, Interval

# ======================= SETTINGS =======================

# Discord webhook URL — comes from a GitHub Secret (see setup guide), not hardcoded here.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

OVERSOLD = 30
OVERBOUGHT = 70
RSI_PERIOD = 14

# File used to remember the last state between runs, so we only alert on
# a NEW crossing, not every time the script runs.
STATE_FILE = "state.json"

# Every RSI value checked gets appended here with a timestamp, so if an
# alert seems to go missing later, we can look back and see exactly what
# the data showed at that moment. Keeps only the most recent MAX_LOG_LINES
# entries so the file doesn't grow forever.
LOG_FILE = "rsi_log.csv"
MAX_LOG_LINES = 5000

# ---- Source 1: TradingView (crypto, forex, gold) ----
# Each entry: display name, TradingView symbol, exchange, and screener type.
TV_WATCHLIST = [
    {"name": "BTC/USD",   "symbol": "BTCUSDT",    "exchange": "BINANCE", "screener": "crypto"},
    {"name": "ETH/USD",   "symbol": "ETHUSDT",    "exchange": "BINANCE", "screener": "crypto"},
    {"name": "XAU/USD",   "symbol": "XAUUSD",     "exchange": "OANDA",   "screener": "cfd"},
    {"name": "EUR/USD",   "symbol": "EURUSD",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "GBP/USD",   "symbol": "GBPUSD",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "USD/JPY",   "symbol": "USDJPY",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "USD/CHF",   "symbol": "USDCHF",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "AUD/USD",   "symbol": "AUDUSD",     "exchange": "OANDA",   "screener": "forex"},
    {"name": "USD/CAD",   "symbol": "USDCAD",     "exchange": "OANDA",   "screener": "forex"},
]

TV_TIMEFRAMES = [
    {"label": "5m", "interval": Interval.INTERVAL_5_MINUTES},
    {"label": "1h", "interval": Interval.INTERVAL_1_HOUR},
]

# ---- Source 2: Yahoo Finance (real indices/commodities TradingView's API can't serve) ----
# Each entry: display name, Yahoo Finance ticker.
YF_WATCHLIST = [
    {"name": "US OIL",     "ticker": "CL=F"},      # WTI Crude — this IS the spot price;
                                                     # brokers' "USOIL" CFDs are themselves
                                                     # derived from this same futures feed.
    {"name": "NASDAQ 100", "ticker": "^NDX"},       # real cash index, not a future
    {"name": "US 500",     "ticker": "^GSPC"},      # real cash index, not a future
    {"name": "XAG/USD",    "ticker": "SI=F"},       # Silver futures — same reasoning as oil:
                                                     # this is the underlying price feed
                                                     # brokers' silver CFDs are derived from.
    {"name": "UK 100",     "ticker": "^FTSE"},      # real cash index, not a future
]

# Yahoo Finance interval strings differ slightly from TradingView's
YF_TIMEFRAMES = [
    {"label": "5m", "yf_interval": "5m"},
    {"label": "1h", "yf_interval": "60m"},
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


def handle_result(key, name, timeframe_label, rsi, state):
    """Shared logic: log the reading, alert on a new crossing, update state."""
    new_status = get_status(rsi)
    old_status = state.get(key, "neutral")

    log_rsi(name, timeframe_label, rsi, new_status)

    if new_status != old_status and new_status != "neutral":
        emoji = "📉" if new_status == "oversold" else "📈"
        send_discord_alert(
            f"{emoji} **{name}** ({timeframe_label}) RSI is **{rsi}** — {new_status.upper()}"
        )

    state[key] = new_status


def check_tv_symbol(entry, timeframe, state):
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
        handle_result(key, entry["name"], timeframe["label"], rsi, state)

    except Exception as e:
        print(f"Error checking {entry['name']} [{timeframe['label']}]: {e}")


def calculate_rsi(closes, period=14):
    """Wilder's RSI — the same standard method TradingView uses internally."""
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def check_yf_symbol(entry, timeframe, state):
    key = f"{entry['ticker']}_{timeframe['label']}"
    try:
        data = yf.download(
            entry["ticker"],
            period="5d" if timeframe["yf_interval"] == "5m" else "60d",
            interval=timeframe["yf_interval"],
            progress=False,
        )
        if data.empty or len(data) < RSI_PERIOD + 1:
            print(f"Not enough data for {entry['name']} [{timeframe['label']}], skipping.")
            return

        closes = data["Close"]
        if isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, 0]

        rsi_series = calculate_rsi(closes, RSI_PERIOD)
        rsi = round(float(rsi_series.iloc[-1]), 2)
        print(f"{entry['name']} [{timeframe['label']}]: RSI = {rsi}")
        handle_result(key, entry["name"], timeframe["label"], rsi, state)

    except Exception as e:
        print(f"Error checking {entry['name']} [{timeframe['label']}]: {e}")


def main():
    state = load_state()

    for entry in TV_WATCHLIST:
        for timeframe in TV_TIMEFRAMES:
            check_tv_symbol(entry, timeframe, state)

    for entry in YF_WATCHLIST:
        for timeframe in YF_TIMEFRAMES:
            check_yf_symbol(entry, timeframe, state)

    save_state(state)


if __name__ == "__main__":
    main()
