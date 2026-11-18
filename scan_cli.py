"""
Headless RSI scanner (for GitHub Actions or terminal).

Examples:
    python scan_cli.py --universe nifty500 --strategy both
    python scan_cli.py --universe fno --strategy GFS --period 14
    python scan_cli.py --universe nifty500 --strategy custom \
        --conditions "month=60-100,week=55-100,day=45-50,15minute=30-40"
    python scan_cli.py --universe list --symbols "RELIANCE,TCS,INFY" --strategy AGFS

Token: set env var UPSTOX_ACCESS_TOKEN (use the Upstox Analytics Token, valid 1 year).
Timeframe keys: month, week, day, 60minute, 30minute, 15minute, 5minute
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import scanner_core as core

PRESET_COND = {
    "GFS": {"month": (60, 100), "week": (60, 100), "day": (38, 43)},
    "AGFS": {"month": (60, 100), "week": (60, 100), "day": (58, 63)},
}


def parse_conditions(text):
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            tf, rng = part.split("=")
            lo, hi = rng.split("-")
            tf = tf.strip()
            if tf not in core.TIMEFRAMES:
                raise ValueError
            out[tf] = (float(lo), float(hi))
        except ValueError:
            sys.exit(f"Bad condition '{part}'. Format: timeframe=min-max, "
                     f"timeframes: {', '.join(core.TIMEFRAMES)}")
    if not out:
        sys.exit("--conditions khali hai.")
    return out


def pick_universe(args):
    eq, fno_keys, fno_syms = core.load_universe()
    if args.universe == "fno":
        eq = eq[eq["instrument_key"].isin(fno_keys) | eq["trading_symbol"].str.upper().isin(fno_syms)]
    elif args.universe == "nifty500":
        syms = None
        if Path(args.nifty500_file).exists():
            d = pd.read_csv(args.nifty500_file)
            syms = set(d["Symbol"].astype(str).str.strip().str.upper())
        else:
            syms = core.fetch_nifty500()
        if not syms:
            sys.exit(f"Nifty 500 list nahi mili. NSE se ind_nifty500list.csv download karke "
                     f"repo me '{args.nifty500_file}' naam se daalo.")
        eq = eq[eq["trading_symbol"].str.upper().isin(syms)]
    elif args.universe == "list":
        wanted = {s.strip().upper() for s in args.symbols.replace("\n", ",").split(",") if s.strip()}
        eq = eq[eq["trading_symbol"].str.upper().isin(wanted)]
    if args.limit:
        eq = eq.head(args.limit)
    return list(zip(eq["instrument_key"], eq["trading_symbol"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", choices=["nifty500", "fno", "all", "list"], default="nifty500")
    ap.add_argument("--symbols", default="", help="comma separated, --universe list ke saath")
    ap.add_argument("--nifty500-file", default="nifty500.csv")
    ap.add_argument("--strategy", choices=["GFS", "AGFS", "both", "custom"], default="both")
    ap.add_argument("--conditions", default="", help='custom: "month=60-100,week=60-100,day=38-43"')
    ap.add_argument("--period", type=int, default=14)
    ap.add_argument("--min-price", type=float, default=50)
    ap.add_argument("--min-volume", type=int, default=100000)
    ap.add_argument("--live", choices=["true", "false"], default="true", help="aaj ki live candle jodo")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="scan_results.csv")
    args = ap.parse_args()

    token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        sys.exit("UPSTOX_ACCESS_TOKEN set nahi hai.")
    live = args.live == "true"

    if args.strategy == "custom":
        strategies = {"Custom": parse_conditions(args.conditions)}
    elif args.strategy == "both":
        strategies = {"GFS": PRESET_COND["GFS"], "AGFS": PRESET_COND["AGFS"]}
    else:
        strategies = {args.strategy: PRESET_COND[args.strategy]}

    rows = pick_universe(args)
    if not rows:
        sys.exit("Scan karne ke liye koi stock nahi mila.")
    print(f"Universe: {args.universe} ({len(rows)} stocks) | RSI period {args.period} | live={live}")

    # Token / API sanity check, taaki expired token par "0 matches" ka jhootha result na aaye
    if core.get_candles(rows[0][0], "day", token, False) is None:
        sys.exit("Upstox se data nahi mila. Token expired/galat hai ya API error. "
                 "Naya Analytics Token generate karke secret update karo.")

    # Ek run me candles dobara fetch na hon (GFS + AGFS dono me same monthly/weekly)
    cache, original = {}, core.get_candles

    def cached(key, tf, tok, live_flag=True):
        k = (key, tf, live_flag)
        if k not in cache:
            cache[k] = original(key, tf, tok, live_flag)
        return cache[k]

    core.get_candles = cached

    frames, summary = [], ["# RSI scan results", ""]
    for name, cond in strategies.items():
        desc = " | ".join(f"{core.TIMEFRAMES[t]['label']} {lo:g}-{hi:g}" for t, (lo, hi) in cond.items())
        print(f"\n=== {name}: RSI({args.period}) {desc} ===")
        found = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(core.analyse, r, cond, args.period, token,
                              args.min_price, args.min_volume, live) for r in rows]
            for i, f in enumerate(as_completed(futs), 1):
                res = f.result()
                if res:
                    found.append(res)
                if i % 100 == 0:
                    print(f"  {i}/{len(rows)} scanned, {len(found)} mile", flush=True)
        df = pd.DataFrame(found)
        summary += [f"## {name}  ({desc})", f"Universe: {args.universe}, {len(rows)} stocks", ""]
        if df.empty:
            print("Koi stock match nahi hua.")
            summary += ["Koi stock match nahi hua.", ""]
            continue
        df = df.sort_values([c for c in df.columns if c.endswith("RSI")][-1]).reset_index(drop=True)
        print(df.to_string(index=False))
        frames.append(df.assign(Strategy=name))
        cols = list(df.columns)
        summary += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        summary += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
        summary.append("")

    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out.to_csv(args.out, index=False)
    print(f"\nSaved {len(out)} rows -> {args.out}")

    summary.append("_Shortlist for research only, not a buy/sell signal._")
    step = os.getenv("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write("\n".join(summary) + "\n")


if __name__ == "__main__":
    main()
