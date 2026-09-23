# FILE: vmc_final_engine_v19_full.py  (ENGINE V20.1 - filename kept for workflow compatibility)
import ccxt, pandas as pd, requests, datetime, os

# --- CONFIGURATION (SECURE - ENV VARIABLES) ---
TOKEN = os.environ.get("TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SYMBOL = 'DOT/USDT'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "pos_state.txt")
DIGEST_FILE = os.path.join(BASE_DIR, "quiet_digest.txt")

# Temporary Line Alert Configs
LINE_ALERT_SLOPED_ACTIVE = True
LINE_ALERT_HORIZ_ACTIVE = True
HORIZ_SUPPORT_LEVEL = 0.9537

# Sloped Line Anchors: Sep 22 03:00 ($1.088) -> Sep 22 14:00 ($1.1373)
SLOPE_P1_TS = 1726974000000  # Sep 22 03:00 UTC+8 in ms
SLOPE_P1_PRICE = 1.088
SLOPE_PER_15M = 0.00112045

def get_gmt8_now():
    tz_gmt8 = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz_gmt8)

def is_quiet_window(dt_gmt8):
    # 23:00 to 05:59 GMT+8
    return dt_gmt8.hour >= 23 or dt_gmt8.hour < 6

def pine_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def zlema(series, length):
    lag = int((length - 1) / 2)
    z_series = series + (series - series.shift(lag))
    return pine_ema(z_series, length)

def calc_impulse_macd(df, length_ma=17, length_sig=9):
    src = (df['o'] + df['h'] + df['l'] + df['c']) / 4.0
    hi = pine_ema(df['h'], length_ma)
    lo = pine_ema(df['l'], length_ma)
    mi = zlema(src, length_ma)
    md = pd.Series(0.0, index=df.index)
    md[mi > hi] = mi - hi
    md[mi < lo] = mi - lo
    sb = md.rolling(length_sig).mean()
    sh = md - sb
    return md, sb, sh

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"pos": "NONE", "last_side": 0, "mfi_delay": 0, "prev_wt_side": 0}
    try:
        with open(STATE_FILE, "r") as f:
            d = f.read().strip().split(',')
            return {"pos": d[0], "last_side": int(d[1]), "mfi_delay": int(d[2]), "prev_wt_side": int(d[3])}
    except:
        return {"pos": "NONE", "last_side": 0, "mfi_delay": 0, "prev_wt_side": 0}

def save_state(pos, last_side, mfi_delay, prev_wt_side):
    with open(STATE_FILE, "w") as f:
        f.write(f"{pos},{last_side},{mfi_delay},{prev_wt_side}")

def append_digest_entry(entry_text):
    with open(DIGEST_FILE, "a") as f:
        f.write(entry_text + "\n---\n")

def read_and_clear_digest():
    if not os.path.exists(DIGEST_FILE):
        return None
    try:
        with open(DIGEST_FILE, "r") as f:
            content = f.read().strip()
        os.remove(DIGEST_FILE)
        return content if content else None
    except:
        return None

def send_telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=30
        )
    except Exception as e:
        print(f"Telegram Delivery Error: {e}")

def get_vmc_signals():
    global LINE_ALERT_SLOPED_ACTIVE, LINE_ALERT_HORIZ_ACTIVE
    try:
        ex = ccxt.binance({'options': {'defaultType': 'future'}})
        ohlcv_h1 = ex.fetch_ohlcv(SYMBOL, timeframe='1h', limit=400)
        ohlcv_m15 = ex.fetch_ohlcv(SYMBOL, timeframe='15m', limit=1000)
        ohlcv_m5 = ex.fetch_ohlcv(SYMBOL, timeframe='5m', limit=400)

        df_h1 = pd.DataFrame(ohlcv_h1, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        df_m15 = pd.DataFrame(ohlcv_m15, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        df_m5 = pd.DataFrame(ohlcv_m5, columns=['ts', 'o', 'h', 'l', 'c', 'v'])

        # --- 1H HTF LAYER ---
        src_h1 = (df_h1['o'] + df_h1['h'] + df_h1['l'] + df_h1['c']) / 4.0
        wt1_h1 = pine_ema(pine_ema(src_h1, 14), 26)
        tp_h1 = (df_h1['h'] + df_h1['l'] + df_h1['c']) / 3.0
        chg_h1 = tp_h1.diff()
        u_h1 = (df_h1['v'] * tp_h1).where(chg_h1 > 0, 0.0).rolling(28).sum()
        l_h1 = (df_h1['v'] * tp_h1).where(chg_h1 < 0, 0.0).rolling(28).sum()
        mfi_h1 = 100 - (100 / (1 + (u_h1 / l_h1.replace(0, 1.0)))) - 50
        h1_wt_val, h1_mfi_val = wt1_h1.iloc[-2], mfi_h1.iloc[-2]
        h1_wt_side = 1 if h1_wt_val > 0 else -1 if h1_wt_val < 0 else 0
        h1_mfi_side = 1 if h1_mfi_val > 0 else -1 if h1_mfi_val < 0 else 0

        # --- 15M SIGNAL LAYER ---
        src_m15 = (df_m15['o'] + df_m15['h'] + df_m15['l'] + df_m15['c']) / 4.0
        esa_m15 = pine_ema(src_m15, 9)
        d_m15 = pine_ema((src_m15 - esa_m15).abs(), 9)
        ci_m15 = (src_m15 - esa_m15) / (0.015 * d_m15)
        wt1 = pine_ema(ci_m15, 12)
        wt2 = wt1.rolling(4).mean()
        wt1_c, wt2_c = wt1.iloc[-2], wt2.iloc[-2]
        wt1_p, wt2_p = wt1.iloc[-3], wt2.iloc[-3]
        cross_up15 = (wt1_p < wt2_p and wt1_c > wt2_c)
        cross_dn15 = (wt1_p > wt2_p and wt1_c < wt2_c)

        # --- 5M TRIGGER LAYER ---
        ema8, ema21 = pine_ema(df_m5['c'], 8), pine_ema(df_m5['c'], 21)
        ema_up = (ema8.iloc[-3] < ema21.iloc[-3]) and (ema8.iloc[-2] > ema21.iloc[-2])
        ema_dn = (ema8.iloc[-3] > ema21.iloc[-3]) and (ema8.iloc[-2] < ema21.iloc[-2])

        # --- IMPULSE MACD MODULE: 15M 17/9 (UNFILTERED) + 1H 34/9 (INFO ONLY) ---
        md15, sb15, sh15 = calc_impulse_macd(df_m15, 17, 9)
        md1, sb1, sh1 = calc_impulse_macd(df_h1, 34, 9)
        md_c, md_p = md15.iloc[-2], md15.iloc[-3]
        sh_c, sh_p = sh15.iloc[-2], sh15.iloc[-3]

        imac_st1_bear = (sh_c > 0 and sh_c < sh_p) and (md_c > 0 and md_c < md_p)
        imac_st2_bear = ((md_p >= 0 and md_c < 0) or (sh_p >= 0 and sh_c < 0))
        imac_st1_bull = (sh_c < 0 and sh_c > sh_p) and (md_c < 0 and md_c > md_p)
        imac_st2_bull = ((md_p <= 0 and md_c > 0) or (sh_p <= 0 and sh_c > 0))

        md1_c, sh1_c = md1.iloc[-2], sh1.iloc[-2]
        if md1_c > 0 and sh1_c > 0: imac_1h_str = "BULL"
        elif md1_c < 0 and sh1_c < 0: imac_1h_str = "BEAR"
        else: imac_1h_str = "MIXED"

        # --- TEMPORARY LINE ALERTS CHECK ---
        curr_low_m15 = df_m15['l'].iloc[-2]
        curr_ts_m15 = df_m15['ts'].iloc[-2]
        line_alert_msg = []

        if LINE_ALERT_HORIZ_ACTIVE and curr_low_m15 <= HORIZ_SUPPORT_LEVEL:
            line_alert_msg.append(f"⚠️ HORIZONTAL SUPPORT HIT @ ${HORIZ_SUPPORT_LEVEL}")
            LINE_ALERT_HORIZ_ACTIVE = False

        if LINE_ALERT_SLOPED_ACTIVE and curr_ts_m15 >= SLOPE_P1_TS:
            bars_elapsed = (curr_ts_m15 - SLOPE_P1_TS) / (15 * 60 * 1000)
            calc_line_price = SLOPE_P1_PRICE + (bars_elapsed * SLOPE_PER_15M)
            if curr_low_m15 <= calc_line_price:
                line_alert_msg.append(f"⚠️ SLOPED TRENDLINE HIT @ ${round(calc_line_price, 4)}")
                LINE_ALERT_SLOPED_ACTIVE = False

        # --- STATE LOADING & RESOLUTION ---
        st = load_state()
        pos, last_side, mfi_delay, prev_wt_side = st["pos"], st["last_side"], st["mfi_delay"], st["prev_wt_side"]

        if h1_wt_side != prev_wt_side and prev_wt_side != 0:
            mfi_delay = 0
        if h1_wt_side != 0 and h1_mfi_side != h1_wt_side:
            mfi_delay += 1
        elif h1_wt_side != 0 and h1_mfi_side == h1_wt_side:
            mfi_delay = 0

        health = "FINE"
        if h1_wt_side != 0 and h1_mfi_side != h1_wt_side:
            health = f"LAGGY ({mfi_delay})" if mfi_delay >= 2 else f"WAITING ({mfi_delay})"

        h1_bull = (h1_mfi_side == 1 and h1_wt_side == 1)
        h1_bear = (h1_mfi_side == -1 and h1_wt_side == -1)

        action = "NO GO"
        if (cross_up15 and wt1_c < -53) and h1_bull and ema_up:
            action, pos, last_side = "GOOD (LONG)", "LONG", 0
        elif (cross_dn15 and wt1_c > 53) and h1_bear and ema_dn:
            action, pos, last_side = "GOOD (SHORT)", "SHORT", 0
        elif (h1_bull or h1_bear) and (wt1_c < -40 or wt1_c > 40):
            action = "WAIT"

        exit_r = "HOLD"
        if pos == "LONG" and cross_dn15:
            exit_r, pos, last_side = "CLOSE LONG", "NONE", 1
        elif pos == "SHORT" and cross_up15:
            exit_r, pos, last_side = "CLOSE SHORT", "NONE", -1
        elif pos == "NONE":
            if last_side == 1: exit_r = "CLOSE-OLD LONG"
            elif last_side == -1: exit_r = "CLOSE-OLD SHORT"

        save_state(pos, last_side, mfi_delay, h1_wt_side)

        bar_time_gmt8 = datetime.datetime.fromtimestamp(
            df_m15['ts'].iloc[-2] / 1000,
            datetime.timezone(datetime.timedelta(hours=8))
        ).strftime('%Y-%m-%d %H:%M GMT+8')

        imac_raw = "NONE"
        if imac_st1_bear: imac_raw = "imac 15min stage1 bear"
        elif imac_st2_bear: imac_raw = "stage 2 imac 15min bear"
        elif imac_st1_bull: imac_raw = "imac 15min stage1 bull"
        elif imac_st2_bull: imac_raw = "stage 2 imac 15min bull"

        imac_str = f"<b>{imac_raw}</b>" if imac_raw != "NONE" else "NONE"
        line_str = "\n".join(line_alert_msg) if line_alert_msg else "NONE"

        return {
            "time": bar_time_gmt8,
            "action": action,
            "health": health,
            "exit": exit_r,
            "wt1": round(wt1_c, 2),
            "imac": imac_str,
            "imac_1h": imac_1h_str,
            "line_alerts": line_str,
            "raw_exit": exit_r,
            "raw_line_count": len(line_alert_msg)
        }
    except Exception as e:
        return {"error": str(e)}

def format_alert_message(data, prefix="🚨 VMC ENGINE V20.1"):
    return (
        f"{prefix}\n"
        f"SYMBOL: {SYMBOL}\n"
        f"TIME: {data['time']}\n"
        f"ACTION: {data['action']}\n"
        f"HEALTH: {data['health']}\n"
        f"EXIT: {data['exit']}\n"
        f"IMAC 15M: {data['imac']}\n"
        f"IMAC 1H: {data['imac_1h']}\n"
        f"LINE ALERTS: {data['line_alerts']}\n"
        f"WT1: {data['wt1']}"
    )

def dispatch_pipeline(data):
    now_gmt8 = get_gmt8_now()
    quiet = is_quiet_window(now_gmt8)

    is_priority_exit = data['raw_exit'] in ["CLOSE LONG", "CLOSE SHORT"]
    is_priority_line = data['raw_line_count'] > 0
    is_bypass_signal = is_priority_exit or is_priority_line

    if not quiet:
        digest_content = read_and_clear_digest()
        if digest_content:
            digest_msg = f"🌅 <b>06:00 GMT+8 QUIET HOURS DIGEST SUMMARY</b>\n\n{digest_content}"
            print("Broadcasting quiet hours digest summary...")
            send_telegram(digest_msg)

    if quiet:
        if is_bypass_signal:
            msg = format_alert_message(data, prefix="🚨 PRIORITY EXIT/LINE ALERT (QUIET HOURS BYPASS)")
            print(f"[QUIET HOURS BYPASS] Sending priority alert:\n{msg}")
            send_telegram(msg)
        else:
            digest_entry = (
                f"[{data['time']}] ACTION: {data['action']} | "
                f"HEALTH: {data['health']} | EXIT: {data['exit']} | "
                f"IMAC 15M: {data['imac']} | IMAC 1H: {data['imac_1h']} | WT1: {data['wt1']}"
            )
            print(f"[QUIET HOURS SUPPRESSED] Queued entry: {digest_entry}")
            append_digest_entry(digest_entry)
        return

    msg = format_alert_message(data)
    print(msg)
    send_telegram(msg)

if __name__ == "__main__":
    sig = get_vmc_signals()
    if "error" not in sig:
        dispatch_pipeline(sig)
    else:
        print(f"Execution Error: {sig['error']}")
