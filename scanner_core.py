"""Core scanning logic (no UI): Upstox data, RSI, universe loaders."""
import gzip
import io
import time
from datetime import date, timedelta
from functools import lru_cache
from threading import Lock

import pandas as pd
import requests

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


@lru_cache(maxsize=1)
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


