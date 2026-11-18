# RSI multi-timeframe scanner (Upstox)

Scan Indian stocks on RSI across multiple timeframes using the Upstox API.
Run it as a local dashboard (Streamlit) or automatically on GitHub Actions.

## Features
- Timeframes: monthly, weekly, daily, 1 hour, 30 / 15 / 5 minute
- RSI period, per-timeframe on/off and RSI min/max are all configurable
- Presets: GFS (daily RSI 38-43) and Advance GFS (daily RSI 58-63), both with monthly and weekly RSI above 60
- Universe: Nifty 500, F&O stocks, all NSE equities, or your own list
- Min price and volume filters, optional live (today's) candle
- Results as CSV, plus a table in the GitHub Actions run summary

## Files
| File | Purpose |
|---|---|
| `scanner_core.py` | Data fetching, RSI, universe loading |
| `gfs_dashboard.py` | Streamlit dashboard (local or hosted website) |
| `scan_cli.py` | Headless scanner used by GitHub Actions |
| `.github/workflows/scan.yml` | Scheduled and manual scan workflow |

## Token (valid for 1 year)
Use the Upstox **Analytics Token**: a read-only token, valid for one year, and market data needs no static IP.
Generate it at the Upstox Developer Apps page (Analytics tab, Generate Token). Only one is active per account; generating a new one revokes the old one.
Set a reminder to renew it before it expires.

## Open as a website (Streamlit Community Cloud)
1. Push this repo to GitHub (private is fine).
2. Go to share.streamlit.io, sign in with GitHub, click **Create app**, pick this repo, branch `main`, main file `gfs_dashboard.py`.
3. Under **Advanced settings > Secrets** paste (see `.streamlit/secrets.toml.example`):
   ```toml
   UPSTOX_ACCESS_TOKEN = "your_upstox_analytics_token"
   APP_PASSWORD = "choose_a_strong_password"
   ```
4. Deploy. You get a link like `https://your-app.streamlit.app` that opens in any browser tab. Bookmark it or add it to your phone's home screen.
5. Add `nifty500.csv` (from NSE) to the repo root so Nifty 500 does not depend on NSE being reachable from the cloud.

The app asks for `APP_PASSWORD` before showing anything, so strangers cannot use your token.
Free apps go to sleep after inactivity; opening the link wakes them in under a minute.

## Run on GitHub Actions
1. Repo **Settings > Secrets and variables > Actions > New repository secret**
   Name: `UPSTOX_ACCESS_TOKEN`, value: your Analytics Token.
2. Download `ind_nifty500list.csv` from NSE and add it to the repo root as `nifty500.csv`
   (NSE can block cloud IPs, so the workflow reads this file first). Refresh it a few times a year.
3. Open the **Actions** tab, choose **RSI scan**, then **Run workflow** and pick universe, strategy, RSI period.
   It also runs automatically Monday to Friday at 16:15 IST.
4. Results: open the run page for a summary table, or download the `scan-results` artifact (CSV).

Custom strategy example (workflow input `conditions`):
`month=60-100,week=60-100,day=45-50,15minute=30-40`

## Run locally
```bash
pip install -r requirements.txt
export UPSTOX_ACCESS_TOKEN="your_token"   # Windows: set UPSTOX_ACCESS_TOKEN=your_token

streamlit run gfs_dashboard.py            # dashboard
python scan_cli.py --universe nifty500 --strategy both   # command line
```

## Notes
- Never commit your token. Keep it only in GitHub Secrets or an environment variable.
- In a public repo, Actions logs and artifacts are public. Use a private repo if you do not want your scan results visible.
- Intraday history limits are set by Upstox. If you get a date-range error, reduce `days` for that timeframe in `TIMEFRAMES`.
- RSI uses Wilder smoothing and can differ slightly from charting platforms.
- GitHub may pause scheduled workflows in repos with no activity for 60 days.

## Disclaimer
For education and research only. Not financial advice; results are a shortlist to research, not buy or sell signals. Not affiliated with Upstox or NSE.
