# Claude Adaptive Trader

A paper-only, evidence-driven research platform for crypto spot strategies. It reads public
Binance market data, classifies the market regime with fixed rules, tests four strategies with
a realistic event-driven backtester, keeps a frozen test set, and trades **simulated** money
on paper with a centralized risk manager. A web interface shows every step.

> **No real money is ever traded.** This version contains no code path that can place a
> real-money order, uses no exchange account and no API key for market data, and has no
> leverage or short selling. All portfolio figures are simulated.
>
> **No profitable edge has been demonstrated.** On the data available while this was built,
> every strategy failed the acceptance gates (see [Research results](#research-results)).
> Treat the platform as a tool to find out whether an edge exists, not as a source of profit.

## Brzi početak (hrvatski)

1. Instaliraj **Python 3.11+** i **Node.js 20.19+** (preporučeno 22).
2. U mapi `claude-adaptive-trader` pokreni naredbe iz odjeljka [Installation](#installation)
   (Windows ili Linux/macOS).
3. Pokreni server: `cd backend` pa `python -m app serve`, zatim otvori
   <http://127.0.0.1:8000> u pregledniku.
4. Na stranici **Market Terminal** preuzmi povijest s Binancea (ili uvezi priložene uzorke),
   na stranici **Backtesting Laboratory** pokreni backtest i validaciju, a na stranici
   **Paper Trading** pokreni simulirano trgovanje.
5. Sve vrijednosti portfelja su **simulirane**. Program ne može trgovati pravim novcem.

## Contents

- [What it does](#what-it-does)
- [Installation](#installation)
- [Starting the application](#starting-the-application)
- [Market data](#market-data)
- [Research workflow](#research-workflow)
- [Paper trading](#paper-trading)
- [Tests](#tests)
- [Configuration](#configuration)
- [Optional Claude research assistant](#optional-claude-research-assistant)
- [Architecture](#architecture)
- [Methodology](#methodology)
- [Research results](#research-results)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)

## What it does

| Page | What you can do |
| --- | --- |
| **Overview** | Simulated equity, cash, realized/unrealized PnL, daily/weekly/monthly returns, win rate, profit factor, expectancy, drawdown, Sharpe/Sortino (only when meaningful), regime per pair, active strategy and version, risk utilisation, Binance feed freshness, recent orders, signals and rejections |
| **Market Terminal** | TradingView Lightweight Charts candles with EMA 20/50/200, Bollinger bands, RSI, ATR and volume; live updates over WebSocket; ticker and order book; data inventory, downloads, CSV import, gaps |
| **Strategy Management** | The four strategies with their rules; versions and their lifecycle (research → validated → approved, approval by a person only); out-of-sample evidence by regime; the adaptive selector's view of each pair; the regime detector's reasoning; the optional research assistant and its proposals |
| **Backtesting Laboratory** | Backtests at 1x/2x/3x costs, the full validation protocol, the selector walk-forward and the one-time final test, all as background jobs; results with equity vs buy & hold, drawdown, gates, walk-forward windows, parameter sensitivity, per-regime results, trades, Markdown/CSV/JSON reports; datasets and every access to the frozen test set |
| **Paper Trading** | Start a live session on Binance public data or a historical replay on stored candles, in adaptive mode or as a forward test of one strategy; pause, resume, stop, close all, kill switch; chart with simulated fills and signals; every decision with all fourteen risk checks |
| **Portfolio & History** | Positions, orders and every completed trade with filters (run, pair, strategy, regime, period) and CSV export |
| **Risk Management** | Configured limits next to the hard caps compiled into the backend, utilisation meters, circuit breakers (with a logged reset), kill switch, recent risk decisions |
| **Performance Analytics** | For any paper session or backtest: equity, drawdown, monthly heatmap, daily PnL, R and PnL distributions, wins/losses, strategy/regime/pair/exit comparisons, fee contribution, exposure, in-sample vs out-of-sample, sensitivity |
| **Logs & Diagnostics** | Structured logs with filters (secrets redacted), health of every component, database integrity, data gaps, jobs, versions |
| **Settings** | Validated runtime overrides of `config.yaml` that can never exceed the hard limits |

## Installation

Requirements: **Python 3.11 or newer** and **Node.js 20.19 or newer** (22 LTS recommended),
both on the `PATH`. Run everything from the `claude-adaptive-trader` folder.

### Linux and macOS

```bash
cd claude-adaptive-trader
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
```

### Windows (PowerShell)

```powershell
cd claude-adaptive-trader
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
```

If PowerShell refuses to run `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call the interpreter directly
as `.venv\Scripts\python.exe` instead of `python` in every command below.

## Starting the application

```bash
cd backend
python -m app serve
```

Open <http://127.0.0.1:8000>. One process serves the API, the WebSocket and the built
interface. The server listens on `127.0.0.1` only; `--host` and `--port` change that, and a
non-loopback host additionally needs `--allow-remote` (not advised: there is no login).

Stop it with `Ctrl+C`. A running paper session resumes automatically on the next start
(`paper.auto_resume`), without trading on the candles it missed.

**Development mode** (hot reload of the interface): keep `python -m app serve` running, then in
a second terminal `cd frontend` and `npm run dev`, and open <http://127.0.0.1:5173>. The dev
server forwards `/api` and `/ws` to port 8000.

## Market data

All market data comes from Binance's **public** endpoints (CCXT for REST, the Binance
WebSocket for live candles and tickers). No API key is needed or used.

```bash
cd backend
# History for the configured pairs and timeframes (closed candles only; gaps are reported, never filled)
python -m app download --pairs BTC/USDT ETH/USDT --timeframes 15m 1h 4h --days 400
# Real sample files that ship with this repository (Binance BTC/USDT 1h, Coinbase BTC/USD 1h)
python -m app import-samples
# Your own CSV: a header with a time column (ts/timestamp/open_time/time/date) and open, high, low, close[, volume]
python -m app import-csv path/to/file.csv --exchange binance --pair BTC/USDT --timeframe 1h
# What is stored, with coverage and gaps
python -m app inventory
```

The same actions are on the **Market Terminal** page. Every candle keeps its source
(`binance-rest`, `binance-ws`, `local-file:<name>`), higher timeframes built from lower ones
are labelled `derived:<tf>`, candles that are still forming are never stored, and data from
another exchange keeps that exchange's name. A series is **stale** when its newest closed
candle is older than one candle plus `exchange.stale_after_seconds`; entries are refused on
stale or incomplete data.

## Research workflow

The intended order, available on the **Backtesting Laboratory** page or from the command line
(run from `backend/`):

```bash
# 1. Explore on the development segment (first 50% of the stored history), at 1x/2x/3x costs
python -m app backtest --strategy momentum_breakout --exchange binance --pairs BTC/USDT --timeframe 1h
# 2. The predefined validation protocol: parameter sensitivity, walk-forward over development +
#    validation, cost stress, bootstrap intervals, random-entry baseline and the acceptance gates
python -m app validate --strategy momentum_breakout --exchange binance --pairs BTC/USDT --timeframe 1h
# 3. Walk-forward test of the adaptive selector itself
python -m app selector-validate --exchange binance --pairs BTC/USDT --timeframe 1h
# 4. Only for a version that passed validation: one look at the frozen test segment (logged)
python -m app final-test --version-id 3 --reason "final evaluation before a paper forward test" --exchange binance --pairs BTC/USDT --timeframe 1h
```

Strategy parameters can be overridden with `--params '{"target_r": 2.5}'`. Every run is stored
as an experiment (configuration snapshot and hash, data period and sources, strategy versions,
software version, seed), failed runs included, with a report under `reports/<experiment id>/`.

A version becomes **validated** only when every gate passes, and **approved** only when a
person approves it on the Strategy Management page (approving an unvalidated version needs an
explicit override, which is labelled as such). The adaptive selector uses approved versions
only.

## Paper trading

On the **Paper Trading** page choose:

- **Live Binance data** (closed candles as they complete) or **Historical replay** (candle by
  candle over stored history, labelled HISTORICAL REPLAY everywhere);
- **Adaptive selector** (approved versions with enough out-of-sample evidence in the current
  regime; otherwise it records "no trade") or **Forward test one strategy**.

Decisions are taken at a candle's close and filled at the next candle's open with modelled
fees, slippage, spread and market impact. State is kept in SQLite, so a restart resumes the
session; deterministic client order ids make duplicate orders impossible. From the command
line, against a running server:

```bash
python -m app paper start --data-mode live --strategy-mode forward_test --strategy trend_following
python -m app paper status
python -m app paper kill-switch-on
python -m app paper stop
```

## Tests

```bash
# Backend: 261 tests (indicators against reference values, no look-ahead, fills, risk checks,
# portfolio identities, backtester, walk-forward, gates, selector, paper restart and duplicates, API)
cd backend
python -m pytest

# Frontend: type checks, unit tests and production build
cd ../frontend
npm run typecheck
npm test
npm run build

# Browser end-to-end tests: a real backend with its own databases (under .e2e/, deleted on
# every run) and the built interface in Chromium
npx playwright install chromium
npm run e2e
```

The end-to-end tests expect the virtual environment at `claude-adaptive-trader/.venv`; set
`CAT_PYTHON` to another interpreter if needed. The GitHub workflow
`.github/workflows/claude-adaptive-trader.yml` runs all three suites.

## Configuration

`config.yaml` (in this folder; `--config` or the `CAT_CONFIG` environment variable select
another file) is validated strictly at start-up: unknown keys, live trading, leverage, short
selling and any value above a hard limit are rejected.

| Section | Contents |
| --- | --- |
| `app` | Name and mode (`paper`; `backtest` is accepted for research runs, live trading is rejected) |
| `exchange` | Pairs, signal timeframe (15m) and confirmation timeframes (1h, 4h), staleness threshold, history length, WebSocket, polling |
| `costs` | Fee (0.10%/side), slippage (0.05%/side), spread, take-profit trade-through, participation cap, market impact, stress multipliers |
| `portfolio` | Initial virtual cash and quote currency |
| `risk` | 0.25% risk per trade (max 0.50%), 1% open risk, exposure and position caps, correlation groups, 1% daily loss limit, 5% drawdown limit, stop distance bounds, cost-to-reward and spread limits, kill switch |
| `regime` | Indicator periods and the detector's thresholds |
| `selector` | Evidence required before a strategy is eligible, hysteresis, recent-performance safeguard |
| `backtest` | Development/validation/test split, walk-forward windows, bootstrap, seed |
| `gates` | Acceptance criteria (300 trades, profit factor 1.2, positive expectancy, survives 2x costs, stability, several environments, not one trade/month, bootstrap interval above zero) |
| `paper` | Polling, auto-resume, replay speed |
| `research` | Optional assistant (off by default) and its model ID |
| `storage`, `server` | Database and report paths; host, port and allowed browser origins |

The **Settings** page writes validated overrides to the database (the file is never edited);
"Reset" returns to the file's values. The hard limits (for example at most 1% risk per trade,
3% open risk, 3% daily loss, 15% drawdown) are compiled into `backend/app/core/limits.py`.

## Optional Claude research assistant

The assistant reviews experiments, explains differences between runs, reviews warning and
error logs, and proposes new experiments. It cannot place or change orders, change settings
or risk limits, or see the frozen test set, and a proposal only becomes a *research* version
after a person approves it. The platform works fully without it.

To enable it: install the requirements (they include the `anthropic` package), set the
`ANTHROPIC_API_KEY` environment variable before starting the server, and on the Settings page
turn on `research.claude_enabled` and set `research.model` to a current Claude model ID from
Anthropic's model documentation. The key is read from the environment only; it is never
stored, logged or sent to the browser.

## Architecture

```
claude-adaptive-trader/
├── config.yaml                 validated configuration
├── requirements.txt            backend dependencies (pinned)
├── backend/app/
│   ├── core/                   hard limits, errors, clock, timeframes, jobs, event bus, security
│   ├── storage/                SQLite (state + candle cache), versioned migrations, settings overrides
│   ├── data/                   Binance public client, validation, candle store, resampling, live feed, CSV import
│   ├── indicators/             EMA/RMA/SMA, RSI, ATR, DMI/ADX, Bollinger, volume, structure (TradingView conventions)
│   ├── regimes/                causal features and the deterministic regime detector
│   ├── strategies/             A trend following, B momentum breakout, C mean reversion, D volatility breakout; selector protocol
│   ├── risk/                   position sizing, circuit breakers, the risk manager (14 checks)
│   ├── execution/              fill simulator, portfolio, trading core shared by backtests and paper trading, paper trader
│   ├── backtesting/            datasets and frozen split, engine, metrics, walk-forward, sensitivity, baselines, gates, reports
│   ├── research/               experiment tracker and version lifecycle, adaptive selector, optional assistant
│   ├── monitoring/             structured logging with secret redaction, health
│   └── api/                    FastAPI routes and the /ws/stream WebSocket
└── frontend/                   React + TypeScript + Vite + Tailwind; Lightweight Charts, Recharts, Lucide
```

Backtests and paper trading run the **same** trading core, risk manager and fill model, so a
strategy cannot behave differently in research and on paper. The API accepts state-changing
requests only from the local interface (loopback host, allowed origin and the `X-CAT-Client`
header).

## Methodology

- **No look-ahead.** Features are causal; higher-timeframe values are joined only after their
  block has closed. Tests recompute every feature on truncated data and with perturbed future
  candles to prove that past values never change.
- **Execution.** Decision at the close, fill at the next open; slippage, half the spread and a
  square-root market-impact term; at most 5% of a candle's volume (partial fills beyond that);
  stops that gap fill at the open; take-profit needs the price to trade through the limit;
  when a candle touches both, the stop is assumed first.
- **Sizing.** Risk budget ÷ stop distance, then the smallest of the cash, liquidity, exposure
  and exchange-rule limits; correlated exposure is a separate check.
- **Evaluation.** A chronological split (50% development, 25% validation, 25% frozen test with
  a fingerprint and an access log); walk-forward windows; costs at 1x, 2x and 3x; bootstrap
  intervals; a random-entry baseline; equal-weight buy & hold with the same costs; results by
  regime and environment. Failing any gate means the verdict "the current system has not
  demonstrated a reliable edge".

## Research results

These are the results actually recorded while this version was built. Binance could not be
reached from the build environment, so the only real data available were the two hourly
sample files in `data/samples` (Binance BTC/USDT 1h, Nov 2020 – May 2021, 4,309 candles;
Coinbase BTC/USD 1h, Jul 2017 – Oct 2019, 20,111 candles). The strategies were designed for a
15-minute signal timeframe; on hourly candles they use 4h and 1d confirmation.

Each row is the predefined validation protocol (`python -m app validate`). The gates are judged
on the **walk-forward out-of-sample** trades over the development + validation span; the frozen
test segments (Binance: Apr – May 2021, Coinbase: Mar – Oct 2019) were never opened, because no
version passed validation.

| Strategy | Data | OOS trades | OOS net return | OOS expectancy | OOS profit factor | Expectancy at 2x costs | Gates passed | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A · Trend following | Binance BTC/USDT 1h | 2 | +0.17% | +0.34 R | 1.68 | +0.21 R | 4 / 11 | no reliable edge |
| B · Momentum breakout | Binance BTC/USDT 1h | 11 | +0.04% | +0.02 R | 1.03 | −0.12 R | 2 / 11 | no reliable edge |
| C · Mean reversion | Binance BTC/USDT 1h | 1 | +0.10% | +0.40 R | undefined (no loss) | +0.26 R | 4 / 11 | no reliable edge |
| D · Volatility breakout | Binance BTC/USDT 1h | 8 | −0.70% | −0.35 R | 0.44 | −0.40 R | 2 / 11 | no reliable edge |
| A · Trend following | Coinbase BTC/USD 1h | 1 | −0.26% | −1.01 R | 0.00 | −1.00 R | 2 / 11 | no reliable edge |
| B · Momentum breakout | Coinbase BTC/USD 1h | 8 | −0.24% | −0.08 R | 0.79 | −0.08 R | 2 / 11 | no reliable edge |
| C · Mean reversion | Coinbase BTC/USD 1h | 1 | −0.26% | −1.01 R | 0.00 | — | 1 / 11 | no reliable edge |
| D · Volatility breakout | Coinbase BTC/USD 1h | 4 | +0.16% | +0.16 R | 1.33 | −0.04 R | 4 / 11 | no reliable edge |

The **adaptive selector** walk-forward (`python -m app selector-validate`) made **0 trades** on
both datasets: no strategy ever had the 30 out-of-sample trades in a regime that eligibility
requires, so it abstained in every window, which is the intended behaviour without evidence.
For comparison, buy & hold over the validation segments returned +20.5% (Binance, Feb – Mar
2021) and −39.2% (Coinbase, Aug 2018 – Mar 2019). A historical replay forward test of B on the
Binance sample made 13 trades and −0.42%; it overlaps the research period, so it is a
demonstration, not independent evidence.

To reproduce: `python -m app import-samples`, then for each strategy
`python -m app validate --strategy <name> --exchange binance --pairs BTC/USDT --timeframe 1h`
(and `--exchange coinbase --pairs BTC/USD`), then `python -m app selector-validate` with the
same scope. Seeds are fixed, so the numbers repeat exactly on the same data, code and exchange
rules (taken from Binance when it is reachable, otherwise from documented assumptions).

**Conclusion: no strategy and no selector configuration has demonstrated a reliable edge.**
The samples are far too small for the 300-trade gate, so even a positive result would not be
evidence. The next step is to download at least a year of 15-minute Binance history for
several pairs, repeat the validation protocol, and forward test on paper before drawing any
conclusion.

## Limitations

- **Live Binance mode was not exercised end-to-end during development**: the build environment
  had no access to Binance (REST and WebSocket were blocked). The client, feed, freshness logic
  and live paper loop are covered by tests with simulated exchange responses, and fail with a
  clear "unreachable" status rather than inventing data.
- Only hourly BTC samples were available for research; ETH and SOL need to be downloaded.
- The strategies are conservative and trade rarely; most candles are classified as
  "uncertain" or produce "no trade" by design.
- Sharpe and Sortino are shown only with at least 30 daily returns; small samples make every
  statistic fragile, and trades are not independent.
- The research assistant was tested with a simulated client; it was not run against the real
  API during development.
- Spot, long-only, one exchange. No leverage, shorting, real-money execution, user accounts or
  remote access; there is no Windows installer for this project (run it with Python and Node).

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `npm ci` fails or Vite refuses to start | Use Node.js 20.19 or newer (`node --version`). |
| "Binance offline" / REST unreachable | Check the internet connection. Binance blocks some countries and cloud IP ranges (for example HTTP 451 from US addresses); the platform keeps working on stored data and says the feed is unreachable. |
| Data is shown as **stale** | No closed candle arrived within the threshold; the feed retries automatically. Entries are refused until the data is fresh again. |
| Port 8000 already in use | `python -m app serve --port 8010`, and open that port. |
| The page says the backend is not reachable | Start `python -m app serve` from `backend/` and keep the terminal open. |
| Errors about missing columns after updating the code | Back up and delete `data/trader.sqlite` (the candle cache `data/market.sqlite` can always be deleted and downloaded again). |
| PowerShell will not activate the virtual environment | See the note under [Windows](#windows-powershell). |
| Research assistant "not available" | It is optional and off by default; see [Optional Claude research assistant](#optional-claude-research-assistant). |
