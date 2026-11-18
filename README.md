# RSI multi-timeframe scanner (Upstox)

A Streamlit dashboard that scans Indian stocks using RSI on multiple timeframes, with data from the Upstox API.

## Features
- Timeframes: monthly, weekly, daily, 1 hour, 30 minute, 15 minute, 5 minute
- RSI period, per-timeframe on/off, and RSI min/max are all editable in the UI
- Presets: GFS (daily RSI 38-43) and Advance GFS (daily RSI 58-63), both with monthly and weekly RSI above 60
- Universe: Nifty 500, F&O stocks only, all NSE equities, or your own list
- Filters for minimum price and volume
- Option to include today's live candle
- CSV download of results

## Setup
```bash
git clone https://github.com/<your-username>/gfs-rsi-scanner.git
cd gfs-rsi-scanner
pip install -r requirements.txt
```

Get an Upstox access token (it expires daily), then either paste it in the sidebar or set it as an environment variable:

```bash
# macOS / Linux
export UPSTOX_ACCESS_TOKEN="your_token"
# Windows (cmd)
set UPSTOX_ACCESS_TOKEN=your_token
```

## Run
```bash
streamlit run gfs_dashboard.py
```

## Notes
- Never commit your access token. `.env` files are already in `.gitignore`.
- Intraday history limits are set by Upstox. If you get a date-range error, reduce `days` for that timeframe in `TIMEFRAMES`.
- RSI uses Wilder smoothing and can differ slightly from charting platforms.
- Nifty 500 is downloaded from NSE. If that fails, upload `ind_nifty500list.csv` in the sidebar.

## Disclaimer
For education and research only. This is not financial advice, and the results are a shortlist to research, not buy or sell signals. Not affiliated with Upstox or NSE.
