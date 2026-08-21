import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# ==========================================
# CONFIGURATION (from GitHub Secrets)
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

SYMBOL = "DOTUSDT"
BTC_SYMBOL = "BTCUSDT"

# Time-bound alert hours in UTC (0 to 8 = 00:00-08:00 UTC Asian Session)
ALERT_HOUR_START_UTC = 0
ALERT_HOUR_END_UTC = 8

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram response: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_binance_klines(symbol, interval, limit=100):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url, timeout=15).json()
    df = pd.DataFrame(data, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
    ])
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['hlc3'] = (df['high'] + df['low'] + df['close']) / 3
    return df

def calculate_wavetrend(df, ch_len=9, avg_len=12):
    ap = df['hlc3']
    esa = ap.ewm(span=ch_len, adjust=False).mean()
    d = (ap - esa).abs().ewm(span=ch_len, adjust=False).mean()
    ci = (ap - esa) / (0.015 * d.replace(0, 0.0001))
    wt1 = ci.ewm(span=avg_len, adjust=False).mean()
    wt2 = wt1.rolling(window=4).mean()
    return wt1, wt2

def run_scanner():
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc}] Scanning {SYMBOL}...")

    # Time gate
    if not (ALERT_HOUR_START_UTC <= now_utc.hour < ALERT_HOUR_END_UTC):
        print(f"Outside alert hours ({ALERT_HOUR_START_UTC}:00-{ALERT_HOUR_END_UTC}:00 UTC). Standing by.")
        return

    # Fetch data from Binance Futures Public API
    df_5m  = get_binance_klines(SYMBOL, "5m", 100)
    df_15m = get_binance_klines(SYMBOL, "15m", 100)
    df_1h  = get_binance_klines(SYMBOL, "1h", 100)
    df_btc = get_binance_klines(BTC_SYMBOL, "1h", 100)

    # BTC filter
    btc_ema50 = df_btc['close'].ewm(span=50, adjust=False).mean().iloc[-1]
    btc_bullish = df_btc['close'].iloc[-1] > btc_ema50
    btc_bearish = df_btc['close'].iloc[-1] < btc_ema50

    # WaveTrend Calculations
    wt1_1h, _ = calculate_wavetrend(df_1h, 14, 26)
    wt1_15m, wt2_15m = calculate_wavetrend(df_15m, 9, 12)
    wt1_5m, wt2_5m   = calculate_wavetrend(df_5m, 9, 12)

    bias = 1 if wt1_1h.iloc[-1] > 8 else (-1 if wt1_1h.iloc[-1] < -6 else 0)

    # Confirmed crossovers on the last CLOSED bar
    cross_up_5m = (wt1_5m.iloc[-2] > wt2_5m.iloc[-2]) and (wt1_5m.iloc[-3] <= wt2_5m.iloc[-3])
    cross_dn_5m = (wt1_5m.iloc[-2] < wt2_5m.iloc[-2]) and (wt1_5m.iloc[-3] >= wt2_5m.iloc[-3])

    # ATR for SL/TP
    tr = np.maximum(
        df_5m['high'] - df_5m['low'],
        np.maximum(
            abs(df_5m['high'] - df_5m['close'].shift()),
            abs(df_5m['low'] - df_5m['close'].shift())
        )
    )
    atr = tr.rolling(14).mean().iloc[-1]
    close_p = df_5m['close'].iloc[-2]  # last CLOSED candle price

    # Signal Logic
    if bias == 1 and btc_bullish and cross_up_5m and wt1_15m.iloc[-1] <= 0:
        sl = round(close_p - (atr * 1.5), 4)
        tp = round(close_p + (atr * 3.0), 4)
        msg = (
            f"🟢 *BUY★ DOTUSDT*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💵 Entry: `{close_p}`\n"
            f"🛑 SL: `{sl}`\n"
            f"🎯 TP: `{tp}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Session: Asian ✅\n"
            f"BTC Trend: Bullish ✅\n"
            f"1H Bias: LONG ✅"
        )
        send_telegram_alert(msg)
        print("Alert Sent: BUY★")

    elif bias == -1 and btc_bearish and cross_dn_5m and wt1_15m.iloc[-1] >= 0:
        sl = round(close_p + (atr * 1.5), 4)
        tp = round(close_p - (atr * 3.0), 4)
        msg = (
            f"🔴 *SELL★ DOTUSDT*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💵 Entry: `{close_p}`\n"
            f"🛑 SL: `{sl}`\n"
            f"🎯 TP: `{tp}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Session: Asian ✅\n"
            f"BTC Trend: Bearish ✅\n"
            f"1H Bias: SHORT ✅"
        )
        send_telegram_alert(msg)
        print("Alert Sent: SELL★")
    else:
        print(f"No signal. Bias={bias} | BTC Bull={btc_bullish} | 5M CrossUp={cross_up_5m} | 5M CrossDn={cross_dn_5m}")

if __name__ == "__main__":
    run_scanner()
