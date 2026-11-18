"""
RSI Multi-Timeframe Scanner Dashboard (Upstox API)
--------------------------------------------------
Setup:
    pip install streamlit requests pandas
Run:
    streamlit run gfs_dashboard.py
"""
import gzip
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from threading import Lock

import pandas as pd
import requests
import streamlit as st

BASE = "https://api.upstox.com/v2"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

BASE_V3 = "https://api.upstox.com/v3"

# Fetch order: bada timeframe pehle, taaki fail hone par API calls bach jayein.
# unit/n Upstox v3 ke hisaab se. "days" = kitne calendar din ka history mangna hai.
# Intraday ke range limits Upstox ke hain (aam taur par 1-15 min: 1 mahina, baaki: 1 quarter);
# agar error aaye to days kam karo.
TIMEFRAMES = {
    "month": {"label": "Monthly", "unit": "months", "n": 1, "days": 365 * 8},
    "week": {"label": "Weekly", "unit": "weeks", "n": 1, "days": 365 * 4},
    "day": {"label": "Daily", "unit": "days", "n": 1, "days": 365},
    "60minute": {"label": "1 Hour", "unit": "hours", "n": 1, "days": 80},
    "30minute": {"label": "30 Minute", "unit": "minutes", "n": 30, "days": 80},
    "15minute": {"label": "15 Minute", "unit": "minutes", "n": 15, "days": 28},
    "5minute": {"label": "5 Minute", "unit": "minutes", "n": 5, "days": 28},
}

DEFAULT_ROW = (False, 0, 100)
PRESETS = {
    "GFS": {"month": (True, 60, 100), "week": (True, 60, 100), "day": (True, 38, 43)},
    "Advance GFS": {"month": (True, 60, 100), "week": (True, 60, 100), "day": (True, 58, 63)},
    "Custom": None,
}

MAX_PER_SEC = 20
_lock = Lock()
_calls = []


def throttle():
    with _lock:
        now = time.time()
        while _calls and now - _calls[0] > 1:
            _calls.pop(0)
        if len(_calls) >= MAX_PER_SEC:
            time.sleep(max(0, 1 - (now - _calls[0])))
        _calls.append(time.time())


def calc_rsi(close: pd.Series, period: int) -> float:
    if len(close) < period + 1:
        return float("nan")
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    out = 100 - 100 / (1 + rs)
    v = out.iloc[-1]
    return float(v) if pd.notna(v) else 100.0


def _fetch(url, token):
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    for attempt in range(3):
        throttle()
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 429:
            time.sleep(1 + attempt)
            continue
        if r.status_code != 200:
            return None
        return r.json().get("data", {}).get("candles", [])
    return None


def get_candles(key, tf, token, live=True):
    """Candles oldest -> newest. Intraday/daily ke liye aaj ki live candle bhi jod sakte hain."""
    meta = TIMEFRAMES[tf]
    unit, n = meta["unit"], meta["n"]
    to_d = date.today()
    from_d = to_d - timedelta(days=meta["days"])
    candles = _fetch(f"{BASE_V3}/historical-candle/{key}/{unit}/{n}/{to_d}/{from_d}", token) or []
    if live and unit in ("minutes", "hours", "days"):
        today = _fetch(f"{BASE_V3}/historical-candle/intraday/{key}/{unit}/{n}", token) or []
        candles = today + candles  # aaj ki candles pehle (newest first)
    if not candles:
        return None
    df = pd.DataFrame(candles).iloc[:, :6]
    df.columns = ["ts", "o", "h", "l", "c", "v"]
    df = df.drop_duplicates("ts", keep="first").sort_values("ts").reset_index(drop=True)
    return df


def analyse(row, conditions, period, token, min_price, min_vol, live=True):
    key, symbol = row
    result = {"Symbol": symbol}
    last_close = last_vol = None
    try:
        for tf in TIMEFRAMES:  # fixed order
            if tf not in conditions:
                continue
            lo, hi = conditions[tf]
            df = get_candles(key, tf, token, live)
            if df is None:
                return None
            val = calc_rsi(df["c"], period)
            if pd.isna(val) or not (lo <= val <= hi):
                return None
            result[f"{TIMEFRAMES[tf]['label']} RSI"] = round(val, 2)
            last_close = float(df["c"].iloc[-1])
            if TIMEFRAMES[tf]["unit"] in ("minutes", "hours"):
                # intraday: poore last session ka volume (ek chhoti candle ka nahi)
                day = df["ts"].astype(str).str[:10]
                last_vol = float(df.loc[day == day.iloc[-1], "v"].sum())
            else:
                last_vol = float(df["v"].iloc[-1])
        if last_close is None or last_close < min_price or last_vol < min_vol:
            return None
        result["Close"] = round(last_close, 2)
        result["Volume"] = int(last_vol)
        return result
    except Exception:
        return None


INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
NIFTY500_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]


@st.cache_data(show_spinner=False, ttl=60 * 60 * 12)
def load_universe():
    """NSE EQ stocks + F&O stocks ki identification (stock futures ke underlying se)."""
    raw = requests.get(INSTRUMENTS_URL, timeout=60).content
    df = pd.read_json(io.BytesIO(gzip.decompress(raw)))
    eq = df[(df["segment"] == "NSE_EQ") & (df["instrument_type"] == "EQ")]
    eq = eq[["instrument_key", "trading_symbol"]].reset_index(drop=True)

    fno_keys, fno_syms = set(), set()
    if "segment" in df.columns:
        fo = df[(df["segment"] == "NSE_FO") & (df["instrument_type"] == "FUT")]
        if "underlying_key" in fo.columns:
            keys = set(fo["underlying_key"].dropna().astype(str))
            fno_keys = {k for k in keys if k.startswith("NSE_EQ")}
        if "underlying_symbol" in fo.columns:
            fno_syms = set(fo["underlying_symbol"].dropna().astype(str).str.upper()) - INDEX_UNDERLYINGS
    return eq, fno_keys, fno_syms


def fetch_nifty500():
    """NSE se Nifty 500 constituents. Fail hone par None."""
    for url in NIFTY500_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if r.status_code == 200:
                d = pd.read_csv(io.StringIO(r.text))
                return set(d["Symbol"].astype(str).str.strip().str.upper())
        except Exception:
            continue
    return None


# ----------------------------- UI -----------------------------
st.set_page_config(page_title="RSI Scanner", layout="wide")
st.title("RSI Multi-Timeframe Scanner")
st.caption("Upstox API | RSI period, timeframe aur range sab badal sakte ho")

# defaults
for tf in TIMEFRAMES:
    en, lo, hi = PRESETS["GFS"].get(tf, DEFAULT_ROW)
    st.session_state.setdefault(f"en_{tf}", en)
    st.session_state.setdefault(f"min_{tf}", float(lo))
    st.session_state.setdefault(f"max_{tf}", float(hi))
st.session_state.setdefault("preset", "GFS")


def apply_preset():
    p = PRESETS[st.session_state["preset"]]
    if p is None:
        return
    for tf in TIMEFRAMES:
        en, lo, hi = p.get(tf, DEFAULT_ROW)
        st.session_state[f"en_{tf}"] = en
        st.session_state[f"min_{tf}"] = float(lo)
        st.session_state[f"max_{tf}"] = float(hi)


with st.sidebar:
    st.header("Settings")
    token = st.text_input("Upstox Access Token", value=os.getenv("UPSTOX_ACCESS_TOKEN", ""), type="password")
    period = st.number_input("RSI Period", min_value=2, max_value=100, value=14, step=1)

    st.subheader("Stock universe")
    mode = st.radio(
        "Source",
        ["Nifty 500", "F&O stocks only", "Saare NSE EQ", "Apni list (paste)"],
        label_visibility="collapsed",
    )
    pasted, n500_file = "", None
    if mode == "Nifty 500":
        n500_file = st.file_uploader(
            "Nifty 500 CSV (optional)", type="csv",
            help="Auto-download fail ho to NSE se ind_nifty500list.csv download karke yahan upload karo. "
                 "CSV me 'Symbol' column hona chahiye.")
    if mode == "Apni list (paste)":
        pasted = st.text_area("Symbols (comma ya new line se alag)", placeholder="RELIANCE, TCS, INFY")
    limit = st.number_input("Max stocks (0 = sab)", min_value=0, value=0, step=100)

    st.subheader("Filters")
    min_price = st.number_input("Min price", min_value=0.0, value=50.0)
    min_vol = st.number_input("Min volume (last session / candle)", min_value=0, value=100000, step=10000)
    live = st.checkbox("Aaj ki live candle jodo", value=True,
                       help="On: aaj ki chalti hui candle bhi RSI me count hogi (intraday aur daily). "
                            "Off: sirf poori ho chuki candles. Extra API call lagti hai.")
    workers = st.slider("Parallel workers", 1, 16, 8)

st.subheader("Strategy conditions")
st.selectbox("Preset", list(PRESETS), key="preset", on_change=apply_preset)
st.caption("Preset chuno ya neeche values khud badlo. Range inclusive hai (min <= RSI <= max). "
           "'> 60' ke liye min 60 aur max 100 rakho.")

conditions = {}
hdr = st.columns([1.2, 1, 1, 1])
hdr[0].markdown("**Timeframe**")
hdr[1].markdown("**Use**")
hdr[2].markdown("**RSI min**")
hdr[3].markdown("**RSI max**")
for tf, meta in TIMEFRAMES.items():
    c = st.columns([1.2, 1, 1, 1])
    c[0].write(meta["label"])
    en = c[1].checkbox("on", key=f"en_{tf}", label_visibility="collapsed")
    lo = c[2].number_input("min", 0.0, 100.0, step=1.0, key=f"min_{tf}", label_visibility="collapsed")
    hi = c[3].number_input("max", 0.0, 100.0, step=1.0, key=f"max_{tf}", label_visibility="collapsed")
    if en:
        conditions[tf] = (lo, hi)

if st.button("Scan chalao", type="primary"):
    if not token:
        st.error("Sidebar me Upstox access token daalo.")
        st.stop()
    if not conditions:
        st.error("Kam se kam ek timeframe on karo.")
        st.stop()

    uni, fno_keys, fno_syms = load_universe()
    if mode == "F&O stocks only":
        uni = uni[uni["instrument_key"].isin(fno_keys) | uni["trading_symbol"].str.upper().isin(fno_syms)]
        if uni.empty:
            st.error("Upstox instrument file se F&O stocks identify nahi ho paye. "
                     "'Apni list (paste)' use karo.")
            st.stop()
    elif mode == "Nifty 500":
        if n500_file is not None:
            try:
                d = pd.read_csv(n500_file)
                syms = set(d["Symbol"].astype(str).str.strip().str.upper())
            except Exception:
                st.error("CSV padh nahi paye. 'Symbol' column check karo.")
                st.stop()
        else:
            syms = st.session_state.get("n500_syms") or fetch_nifty500()
            if syms:
                st.session_state["n500_syms"] = syms
        if not syms:
            st.error("Nifty 500 list auto-download nahi hui. Sidebar me CSV upload karo "
                     "(NSE site se ind_nifty500list.csv) ya 'Apni list (paste)' use karo.")
            st.stop()
        uni = uni[uni["trading_symbol"].str.upper().isin(syms)]
    elif mode == "Apni list (paste)":
        wanted = {s.strip().upper() for s in pasted.replace("\n", ",").split(",") if s.strip()}
        uni = uni[uni["trading_symbol"].str.upper().isin(wanted)]
    if limit:
        uni = uni.head(int(limit))
    rows = list(zip(uni["instrument_key"], uni["trading_symbol"]))
    if not rows:
        st.warning("Scan karne ke liye koi stock nahi mila.")
        st.stop()

    st.write(f"Universe: **{mode}** ({len(rows)} stocks)")
    st.write("Active conditions: " + " | ".join(
        f"{TIMEFRAMES[t]['label']} RSI({int(period)}) {lo:g}-{hi:g}" for t, (lo, hi) in conditions.items()))
    bar = st.progress(0.0, text=f"0/{len(rows)}")
    found = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(analyse, r, conditions, int(period), token, min_price, min_vol, live) for r in rows]
        for i, f in enumerate(as_completed(futs), 1):
            res = f.result()
            if res:
                found.append(res)
            if i % 10 == 0 or i == len(rows):
                bar.progress(i / len(rows), text=f"{i}/{len(rows)} scanned | {len(found)} mile")
    bar.empty()
    st.session_state["results"] = pd.DataFrame(found)

res = st.session_state.get("results")
if res is not None:
    if res.empty:
        st.info("Koi stock in conditions se match nahi hua.")
    else:
        sort_col = [c for c in res.columns if c.endswith("RSI")][-1]
        res = res.sort_values(sort_col).reset_index(drop=True)
        st.success(f"{len(res)} stocks mile")
        st.dataframe(res, use_container_width=True)
        st.download_button("CSV download", res.to_csv(index=False), "scan_results.csv", "text/csv")
st.caption("Ye sirf shortlist tool hai, buy/sell signal nahi. Entry se pehle apna analysis karo.")
