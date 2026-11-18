"""
RSI Multi-Timeframe Scanner Dashboard (Upstox API)
Setup: pip install -r requirements.txt
Run:   streamlit run gfs_dashboard.py
"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from scanner_core import DEFAULT_ROW, PRESETS, TIMEFRAMES, analyse, fetch_nifty500, load_universe

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
