# BTC spot trading bot

An automated BTC/USDT spot trading bot: it pulls candles from a real exchange through
[ccxt](https://github.com/ccxt/ccxt), evaluates a pluggable strategy on every closed
candle, and routes every order through a strict risk manager. It runs in **paper mode by
default**, persists all state in SQLite so it survives restarts, and is built to be left
unattended.

> **This bot can lose money.** Backtest results are hypothetical and do not predict
> future returns. The reference strategy is a plain EMA crossover: it is a working,
> tested example of the plumbing, not an edge. Paper trade for weeks before you risk
> real funds, and only risk what you can afford to lose.

## What is in the box

| Module | Responsibility |
| --- | --- |
| `bot/data/` | Rolling window of **closed** candles, gap backfill, retries with backoff; historical download with a CSV cache |
| `bot/strategy/` | `Strategy` base class (`on_candle(df) -> Signal`), indicators (EMA, RSI, ATR, Donchian, Bollinger, ADX), six strategies including a regime switch and a buy-and-hold benchmark |
| `bot/risk/` | `RiskManager`: fixed-fractional sizing, max position, daily-loss halt, loss-streak cooldown, min interval, kill switch |
| `bot/execution/` | `PaperExchange` (simulated market and resting limit fills, fees, slippage), `LiveExchange` (ccxt), idempotent order ids, `OrderExecutor` |
| `bot/state/` | SQLite: orders, trades, equity curve, key/value bot state |
| `bot/engine.py` | `TradingEngine`: candle → strategy → risk → execution → position bookkeeping; startup reconciliation |
| `bot/backtest/` | Runs the *same* engine, risk manager and paper exchange over historical candles; metrics, parameter search, walk-forward, robustness |
| `bot/notify/` | Telegram / Discord notifications for fills, halts and errors (silent when not configured) |
| `bot/main.py` | The unattended loop: one structured JSON log line per cycle, never dies on a single failure |
| `bot/ui/` | Local web dashboard and the desktop entry point (`python -m bot.ui`, `BTCBot.exe`) |

Spot only. No leverage, no margin, no shorting.

## Desktop app (Windows .exe)

Every push builds `BTCBot.exe` on GitHub Actions (workflow "Build Windows app"). Download
it from the workflow run's **Artifacts** (`BTCBot-windows`), or from the **Releases** page
when a version tag such as `v0.2.0` is pushed. Unzip and double-click `BTCBot.exe`:

- The app opens a dashboard window (native window through WebView2, or your default
  browser if that is unavailable). Nothing is exposed to the network: the UI listens on
  `127.0.0.1` only, on a random port, with a per-run token.
- On first start it creates `config.yaml`, a `data/` folder and the sample candles next to
  the exe. Everything the app writes (SQLite state, `.env` with your keys, logs, the kill
  switch) stays in that folder.
- **Start (paper)** trades with real market data and simulated fills. **Demo** replays the
  bundled historical candles at several candles per second so you can watch it work.
  **Kill switch** blocks every order until you turn it off. **Quit** stops the bot cleanly.
- Tabs: **Dashboard** (equity curve, last decision, risk state), **Chart** (candlesticks
  from the exchange with EMA lines, volume, the open position's entry/stop/take-profit
  levels, entry and exit markers, crosshair tooltip, scroll to zoom, optional TradingView
  embed; on Binance the forming candle and price stream live over the public WebSocket),
  **Trade** (place orders by hand), **AI** (Claude's status and reasoning), **Modes**
  (safe / balanced / aggressive), **History**, **Backtest**, **Settings**, **Log** and
  **Debug**.
- Four themes (Obsidian, Midnight, Terminal, Daylight), animations that can be turned off,
  and toast notifications for fills, trades and risk events.
- A splash screen while Windows unpacks the executable, an animated boot screen while the
  dashboard connects, and a "what's new" panel after every update (`CHANGELOG.md`).
- Pressing **Update** downloads, verifies, installs and restarts the app in one step.
- A "Why no trades yet?" panel that breaks down every decision the bot made and says what
  it is waiting for.
- `BTCBot-console.exe` is the same app with a console window, useful when something fails
  before the UI appears. Logs also go to `data/bot.log`.

Windows SmartScreen will warn about an unsigned executable the first time: choose
"More info" → "Run anyway". Some antivirus tools flag PyInstaller builds; build it yourself
from this repository if you prefer (`pip install -r requirements.txt -r requirements-gui.txt`
then `pyinstaller --clean --noconfirm btcbot.spec`).

To run the same UI from source on any OS: `python -m bot.ui` (add `--browser` to skip the
native window).

### Self-updates

Every push to the branch builds the executables and publishes a GitHub Release tagged
`vX.Y.Z-build.N` (version from `bot/ui/app.py`, build number from the workflow run) with
`BTCBot.exe`, `BTCBot-console.exe`, `BTCBot-windows.zip` and `SHA256SUMS.txt`. The desktop
app checks the latest release at start and every `update.check_interval_minutes`:

- a newer build shows a banner in the app (and a Telegram/Discord message if configured);
- with `update.auto_install: true` it installs itself while the bot is stopped: it downloads
  the executable, verifies its SHA-256 against the release, renames the running exe to
  `BTCBot.old.exe`, puts the new one in place and restarts. Settings, keys, history and the
  kill switch live next to the exe and are untouched;
- while the bot is running it only notifies; "Update & restart" stops the bot cleanly,
  updates, and the restarted app reconciles its position from the SQLite state.

Set `update.enabled: false` to turn this off, or start with `--no-update`. A private
repository needs a read-only `GITHUB_TOKEN` in `.env` (Settings → Updates).

## Quick start (paper trading)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional for paper mode; needed for notifications / live
python -m bot.main              # paper mode: real market data, simulated fills
```

Paper mode needs no API keys: it reads public market data from the exchange in
`config.yaml` (`binance` by default) and simulates fills with the configured fee and
slippage. The bot prints its config at startup, reconciles state, then logs one JSON
object per cycle, for example:

```json
{"ts":"...","event":"cycle","cycle":59,"mode":"paper","candle_time":"2020-11-22T22:00:00Z","close":18613.41,
 "signal":{"action":"BUY","confidence":1.0,"reason":"EMA cross up with RSI in range (...); ema_fast=18473.62 ema_slow=18464.47 rsi=56.3 atr=200.03 close=18613.41",...},
 "decision":{"intent":{"side":"buy","qty":0.1343,"kind":"entry",...}},
 "order":{"client_order_id":"botf2c53100fa3aae1ed536b264","filled":0.134311,"avg_price":18622.72,...},
 "equity":9995.02,"position":{"qty":0.134311,"entry_price":18622.72,"stop_loss":18213.36,"take_profit":19213.49,...},
 "risk":{"kill_switch":false,"halted":false,"cooldown":false,"consecutive_losses":0,"daily_drawdown_pct":-0.05,"trades_today":1}}
```

Useful flags: `--once` (one cycle, then exit), `--max-cycles N`, `--log-format text`,
`--config path/to/config.yaml` (or set `BOT_CONFIG`).

### Offline / replay mode

Without network access, or to see the whole loop run quickly, replay a CSV of candles
through the real loop with a simulated clock. Fills are still paper fills:

```bash
python -m bot.main --replay data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv --max-cycles 1500
```

## How a cycle works

1. Every `poll_interval_seconds` the bot fetches the ticker and checks the open position's
   stop loss / take profit at the market price (exits do not wait for the candle to close).
2. Shortly after a candle closes (`candle_close_grace_seconds`) it fetches OHLCV, keeps only
   fully closed candles, backfills gaps, and hands the window to the strategy.
3. The strategy returns `BUY`, `SELL` or `HOLD` with a confidence and a reason that cites the
   indicator values.
4. The risk manager either rejects the signal (with a reason that is logged) or turns it into
   an `OrderIntent` with a size.
5. The executor derives a client order id from `(strategy, symbol, candle timestamp, side)`,
   stores the order **before** sending it, sends it, waits for the fill, and cancels any
   remainder after `order_timeout_seconds`. Re-evaluating the same candle after a restart
   produces the same id, so a candle can never produce two entries.
6. Position, risk state, equity and the last processed candle are written to SQLite.

If the bot was down for several candles, it evaluates only the newest closed candle but
checks stops against the high/low of every candle it missed.

### Startup reconciliation

Before trading, `TradingEngine.reconcile()`:

- resolves every order the store still lists as open against the exchange (applies fills
  it finds, cancels what is still open);
- cancels open orders on the exchange that carry the bot's id prefix but are unknown locally;
- compares the recorded position with the base-currency balance and shrinks or clears the
  position if the coins are not there (a warning is logged);
- ignores, but reports, coins in the account that the bot did not buy.

## Research: does the strategy actually work?

A single backtest proves very little. Try enough parameter sets and one will look
brilliant on the period you tried it on. Two tools guard against that, in the **Research**
tab or from the command line:

```bash
python -m bot.backtest.optimize --csv data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv --mode grid
python -m bot.backtest.optimize --csv data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv --mode walk-forward
```

**Grid search** backtests every combination in a search space and ranks them. Useful to
see the whole surface, but every number it prints is in-sample.

**Walk-forward** is the honest test: it cuts the history into blocks, optimises on each
block and then trades those settings on the *next* block, which the search never saw. The
equity curve it reports is stitched from out-of-sample results only, and it ends with a
verdict in plain words, including "do not trade it with real money" when the result does
not hold up.

On the shipped sample data the reference strategy fails that test: it is roughly flat out
of sample while buying and holding made far more. That is the answer the tool exists to
give you.

**Robustness** asks the two questions a single result cannot answer:

```bash
python -m bot.backtest.robustness --csv data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv
```

*Would this have worked in a different order?* The trades are re-dealt thousands of times
(bootstrap with replacement, or the same trades reshuffled) and the report gives the median
outcome, the bad fifth, the worst drawdown you should expect, how often the account ended
down, and how often it lost half its value. A strategy that loses in 40% of orderings is a
coin flip with extra steps, whatever its single backtest said.

*Does it survive a nudge?* Each parameter is swept one at a time. If the profit only exists
at one exact value — a **spike** — that value was chosen for this history and will not
repeat. A wide, boring **plateau** is what a real edge looks like, and the middle of one is
a better setting than the peak.

Backtests run about nine times faster than before because indicators are computed once per
run rather than once per candle; the values are identical (there is a test for that).

## Exits: what closes a trade

Entries get most of the attention, but exits usually decide the result. Under `exits` in
`config.yaml`, all measured in multiples of the ATR at entry, zero meaning off:

| Setting | What it does |
| --- | --- |
| `trailing_atr_mult` | Follows the highest price since entry, never moving down |
| `breakeven_after_atr` | Moves the stop to entry plus fees once the trade is this far ahead |
| `partial_take_fraction` | Sells this share of the position early, letting the rest run |
| `partial_take_atr` | The profit at which that partial sale happens |

Within a single candle the stop is always checked before a new high can raise it, because
the order of the high and the low inside a candle is unknowable.

The strategy also takes `trend_filter_period`: with it set, a buy only happens when price
is above that moving average.

## Phone control and alerts

With a Telegram bot token and chat id in `.env`, the bot answers commands from that chat
and only that chat: `/status`, `/pnl`, `/position`, `/why`, `/stop`, `/start`, `/kill`,
`/unkill`, `/help`.

A watchdog alerts you when the bot stops producing cycles while it is supposed to be
trading, because a bot that dies quietly leaves a position with nothing watching it. Set
`notify.daily_report_hour_utc` for a daily summary of equity, trades and realised profit.

## Analytics and exports

The **Analytics** tab shows realised profit by month, expectancy per trade, results split
by exit type, win and loss streaks and holding times, all computed from the trades the bot
actually made, fees included. Both the trade list and the equity curve export to CSV.

## License keys

Live trading needs a key (`BTCB-XXXXX-XXXXX-XXXXX-XXXXX`), entered once under Settings.
Paper trading, backtesting, the charts and the AI trader all work without one, so the bot
can be evaluated safely before buying.

The build ships only the SHA-256 hash of each valid key (`bot/license_hashes.json`), so
the executable carries nothing that could mint new keys, and activation works offline.
Generate and re-issue keys with:

```bash
python scripts/make_licenses.py --count 100      # replaces the whole list
python scripts/make_licenses.py --count 20 --add # keeps the existing keys valid
```

That writes `licenses-private.csv` with the keys themselves. **Keep that file private and
never commit it** (it is in `.gitignore`); ship keys to buyers from there, and use the
`issued_to` column to record who has which. Re-running without `--add` invalidates every
previously issued key on the next build.

This is honest licensing, not copy protection: keys can be shared, and anyone who can edit
the source can bypass the check. It exists to make ownership clear.

## Trading modes

One click in the **Modes** tab rewrites the risk limits and strategy parameters:

| | Safe | Balanced | Aggressive |
| --- | ---: | ---: | ---: |
| Risk per trade | 0.5% | 1% | 2% |
| Max position | 10% of equity | 25% | 50% |
| Daily loss stop | 2% | 3% | 6% |
| Losses before cooldown | 2 | 3 | 5 |
| Cooldown | 12 h | 4 h | 1 h |
| Minimum gap between entries | 4 h | 1 h | 15 min |
| Minimum signal confidence | 0.70 | 0.50 | 0.35 |

Aggressive is not "better": it takes more trades, sizes them larger and lets the account
fall further before stopping. A mode is a starting point; edit any value afterwards in
Settings and the badge shows the config as custom.

## The AI trader

Set `strategy.name: ai` (or use the toggle in the **AI** tab) and put an Anthropic API key
in `.env` as `ANTHROPIC_API_KEY`. On every closed candle the bot sends Claude a structured
snapshot: the last 60 candles, EMA / RSI / ATR, recent range and volume, the open position
and the risk state. Claude returns one JSON decision: action, confidence, stop loss, take
profit and a one-sentence reason citing the indicator values.

The model **never places an order**. Its decision becomes a `Signal` that goes through the
same `RiskManager` as any other strategy, so position size, the daily-loss halt, cooldowns,
the minimum gap between trades and the kill switch all still apply. The bot also refuses a
BUY without a valid stop below the price, and tightens a stop wider than `max_stop_atr`
multiples of ATR.

Cost control: `only_on_technical_setup: true` calls the model only when the technical
strategy sees a setup. `fallback_to_technical: true` (the default) means an API failure or
a missing key falls back to `ema_rsi` instead of stopping the bot. Model choice is yours;
`claude-opus-5` is the default, `claude-sonnet-5` and `claude-haiku-4-5` are cheaper.

The AI strategy is not backtested: replaying a year of candles would mean one API call per
candle. Test it in paper mode.

## Manual trading

The **Trade** tab places market orders through the same executor the bot uses, so fills,
fees and history are recorded identically (simulated in paper mode, real in live mode).
You can size the order as a share of your cash, set a stop and target from presets, see
what the order risks before confirming, move the levels of an open position, and sell
everything in one click.

Manual orders skip the signal filters (confidence, cooldown, daily halt) because they are
your decision, but they still obey the kill switch, the exchange minimums and your actual
balance. The automatic strategy keeps running: it can close a position you opened by hand.
Stop the bot or turn on the kill switch if you want full control.

## Configuration

Everything lives in [`config.yaml`](config.yaml); nothing in the strategy or risk code is
hardcoded. Defaults: 1h candles, BTC/USDT, 1% risk per trade, 3% max daily loss, 25% max
position. The important knobs:

| Key | Meaning |
| --- | --- |
| `exchange.id`, `symbol`, `timeframe` | Any ccxt spot exchange; symbol as `BASE/QUOTE` |
| `exchange.fee_rate`, `maker_fee_rate`, `slippage_bps` | Costs applied to paper fills and backtests |
| `exchange.order_type` | `market` (always fills, taker fee) or `limit` (posts away from the price, maker fee, may miss) |
| `exchange.limit_offset_bps`, `limit_fallback_market` | How far a limit order is posted, and whether a miss falls back to a market order |
| `risk.risk_per_trade_pct` | Equity at risk between entry and stop (fixed-fractional sizing) |
| `risk.max_position_pct` | Cap on position notional as % of equity |
| `risk.max_daily_loss_pct` | Equity down this much from the UTC day's start → no new entries until the next UTC day |
| `risk.max_consecutive_losses`, `cooldown_minutes` | Loss streak → no new entries for the cooldown |
| `risk.min_seconds_between_trades` | Minimum spacing between entries |
| `risk.min_confidence` | Signals below this confidence are treated as HOLD |
| `risk.kill_switch_file` | If this file exists, **no order is placed at all**, entries and exits alike |
| `strategy.name`, `strategy.params` | Strategy and its parameters (see `bot/strategy/ema_rsi.py`) |

Risk limits gate *entries*. Exits (a SELL signal, a stop loss, a take profit) are always
allowed during a halt or cooldown, so the bot can always leave a position. The one
exception is the kill switch, which is an operator override: create the file
(`touch data/KILL_SWITCH`) and the bot stops placing anything until you delete it. If you
need to flatten while it is active, do it by hand on the exchange.

Secrets never go in `config.yaml`. See [`.env.example`](.env.example):

| Variable | Purpose |
| --- | --- |
| `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`, `EXCHANGE_API_PASSWORD` | Exchange credentials (live mode only) |
| `LIVE_TRADING` | Must be `true` to place real orders (see below) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL` | Optional notifications |
| `BOT_CONFIG` | Alternative config path |

## Strategies

| Name | What it buys | Where it fails |
| --- | --- | --- |
| `ema_rsi` | The fast EMA crossing above the slow one while RSI sits in `[rsi_buy_min, rsi_buy_max]` | Choppy markets: it crosses back and forth |
| `breakout` | A close above the highest high of the last `entry_period` candles; exits below the `exit_period` low | Ranges: every false break is a small loss |
| `mean_reversion` | A close below the lower Bollinger band with RSI oversold, but only while the long EMA is rising | Sustained downtrends: every dip keeps dipping |
| `regime` | Whichever of the two above suits the market, chosen by ADX | Whipsaw regimes, where ADX flips around the thresholds |
| `buy_hold` | Once, at the start | Nothing — that is the point: it is the benchmark to beat |
| `ai` | What Claude decides from the same indicators, with a technical fallback | Costs money per candle and is no more clairvoyant than its prompt |

The two new rule-based strategies fail in exactly each other's conditions, which is what
makes `regime` worth having: ADX at or above `trend_above` means trend (breakout takes it),
at or below `range_below` means range (mean reversion takes it), and in between it does
nothing. Whichever half opens a position keeps managing its exit, even if the regime flips.

Two details worth knowing, because they are easy to get wrong:

- The Donchian channel excludes the current candle. Otherwise the candle's own high defines
  the level it is supposed to break, and the strategy "breaks out" every single candle.
- `mean_reversion`'s trend filter requires the long EMA to be **rising**, not price to be
  above it. A dip is below the average by definition, so the usual filter would block every
  entry it exists to allow.

Add a strategy by subclassing `bot.strategy.base.Strategy`, implementing `warmup` and
`on_candle`, and registering it in `bot/strategy/__init__.py`. Strategies must be pure:
same candles in, same signal out. Parameters left over from a strategy you switched away
from are dropped with a warning rather than stopping the bot.

## Entry filters: when *not* to trade

Every strategy answers "is this a setup?". The `filters` section answers the other
question, "is this a market worth taking a setup in?", and it applies to whichever strategy
is selected, the AI one included. Filters only ever block an entry — an exit is never
filtered, because a position that needs to close has to close.

| Key | What it blocks |
| --- | --- |
| `htf_factor`, `htf_period`, `htf_mode` | Entries that disagree with a longer timeframe (24 = daily when trading 1h) |
| `htf_slope_lookback` | How many higher-timeframe blocks "rising" is measured over (1 flips on a single block) |
| `min_atr_pct`, `max_atr_pct` | A market that is barely moving, or moving too wildly to size a stop on |
| `hours_utc`, `skip_weekends` | Hours and days you do not want to be in the market |

Measured on both sample files with the `breakout` strategy, this is what each one is worth
(the shipped default is the daily trend filter plus skipping weekends — the only two that
improved *both* files):

| | win rate | per trade | return | max drawdown |
| --- | --- | --- | --- | --- |
| breakout, no filters (binance) | 39.3% | +19.54 | +10.94% | −8.0% |
| + daily trend rising, no weekends | **48.4%** | **+33.49** | +10.38% | −6.9% |
| breakout, no filters (coinbase) | 37.6% | +14.98 | +36.99% | −15.3% |
| + daily trend rising, no weekends | **44.0%** | **+40.28** | +43.91% | **−6.3%** |

A filter that reads a daily trend needs weeks of hourly candles, so `exchange.candle_history`
has to be at least as long. Selecting a trading mode raises it for you, and both the live
bot and the backtester refuse to run — loudly — rather than quietly answering "not ready"
on every candle.

## The time stop

`exits.time_stop_candles` closes a position that has gone nowhere: after N candles, if the
trade is less than `time_stop_min_atr` ATR in profit, it is sold. On the binance sample it
took the win rate from 39.3% to 50.0% and the drawdown from −8.0% to −5.6%; on the coinbase
sample it raised the win rate but cost some profit per trade. It is off by default for that
reason, and on in the safe and aggressive modes.

## Why the win rate is the wrong target

The win rate is the easiest number in trading to improve and the easiest to be fooled by.
Moving the take profit from 3 ATR to 0.75 ATR on the reference strategy takes it from 36.6%
to 64.8% — and the account from +0.85% to −6.19%, because the average win collapses from
+92.94 to +16.34 while the average loss stays where it was.

What matters is **expectancy**: win rate × average win − loss rate × average loss. Every
backtest reports it, the Research tab can rank by it, and the win-rate field there is a
*floor* (discard anything that wins too rarely to sit through) rather than something to
maximise. Entry quality is the only lever that raises both.

## Market or maker orders

| `exchange.order_type` | What happens | The cost |
| --- | --- | --- |
| `market` (default) | Crosses the spread immediately | Always the taker fee, plus slippage |
| `limit` | Posts `limit_offset_bps` below the price to buy (above to sell) | The lower maker fee, but the trade may never happen |

A maker order only fills if the market comes to it. The backtester models that honestly: a
limit order rests through the next candle and fills only if that candle's range reaches it,
at the limit price with no slippage. Entries that never fill are simply missed — which is
the real cost of saving the fee, and the reason a backtest that assumed every limit filled
would be worthless. Set `limit_fallback_market: true` to cross the spread instead of
skipping a missed entry.

Exits are always market orders. A stop, a take profit, or a sell signal has to happen;
posting one and hoping the price comes back is how a small loss becomes a large one.

## Backtesting

```bash
python -m bot.backtest --strategy ema_rsi --from 2023-01-01 --to 2024-12-31
```

This downloads candles from the configured exchange (paginated, cached under
`data/history/`) and runs them through the same engine, risk manager and fee/slippage model
as live trading. Signals are computed on a closed candle and filled at the next candle's
open; stops and take-profits fill at their level, or at the open when a candle gaps through
them. Options: `--csv file` (offline data), `--param key=value` (override strategy params),
`--cash`, `--timeframe`, `--symbol`, `--exchange`, `--json`, `--show-trades N`.

Two files of real hourly candles ship in `data/samples/` so a backtest works offline:

```bash
python -m bot.backtest --strategy ema_rsi --csv data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv
python -m bot.backtest --strategy ema_rsi --csv data/samples/coinbase_BTCUSD_1h_2017-07_2019-10.csv --from 2018-01-01
```

Results with the default `config.yaml` (fee 0.1%, slippage 5 bps, 1% risk, 25% max position):

| Data | Period | Return | Buy & hold | Max DD | Sharpe | Trades | Win rate | Profit factor | Exposure |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Binance BTC/USDT 1h | 2020-11-20 → 2021-05-16 | +0.85% | +158.9% | -4.15% | 0.26 | 71 | 36.6% | 1.04 | 23.7% |
| Coinbase BTC/USD 1h | 2018-01-04 → 2019-10-17 | -20.41% | -45.0% | -21.16% | -1.88 | 225 | 32.0% | 0.65 | 19.9% |

Read that honestly: with these defaults the reference strategy roughly breaks even in a
strong bull market and loses in a bear market. The risk limits do what they are meant to
(drawdown stays small relative to the market), but the signal itself is not profitable
after fees. Treat it as the starting point for your own research, not as something to
fund.

The Binance sample contains a few one-to-three-hour holes from real exchange downtime;
the backtester runs straight through them, the live feed logs them and restarts its
indicator window after the hole.

## Going live

Live trading needs **two** independent switches, and the bot refuses to start if only one
is set:

1. `LIVE_TRADING=true` in the environment, and
2. `exchange.live: true` in `config.yaml`.

Both set without API keys is also refused. Before you flip them:

- Create the API key with **trade and read permissions only. Do not enable withdrawals.**
  Restrict it to your server's IP if the exchange offers that.
- Fund the account only with what the bot should manage; coins already in the account are
  ignored but reported at startup.
- Run paper mode for weeks and read the equity curve in `data/bot.sqlite` (`equity` table).
- Know where the kill switch is (`data/KILL_SWITCH`) and test it once.
- Set up Telegram or Discord so halts and errors reach you.
- Some exchanges support a testnet: `exchange.sandbox: true` uses it through ccxt.

The startup log prints a `live_mode` warning when real orders are enabled.

## Docker

```bash
cp .env.example .env         # fill in what you need
docker compose up -d --build
docker compose logs -f bot
touch data/KILL_SWITCH       # stop all orders; rm it to resume
```

`./data` is mounted into the container for the SQLite state and the kill switch;
`config.yaml` is mounted read-only. `docker compose down` sends SIGTERM and the bot exits
cleanly after persisting state.

## State and restarts

`data/bot.sqlite` holds `orders`, `trades`, `equity` (one row per closed candle) and
`bot_state` (position, risk state, last processed candle, paper balances). Delete the
file to start a paper session from scratch. Never delete it while a live position is open;
reconciliation will recover the position from the exchange balance, but not its stop
levels.

## Tests

```bash
pytest
```

Unit tests cover strategy signals, indicators, every risk rule, paper fills (fees,
slippage, partial fills, duplicate ids), the idempotent order id, the executor's
crash-recovery path, the engine (entry, stop, take profit, restart, reconciliation), the
candle feed (closed-candle detection, gap backfill, retries), CSV loading, the backtester
and metrics, config loading and the live-safety switches, and the main loop in replay
mode. No test touches the network.

## Layout

```
bot/
  main.py            unattended loop            bot/backtest/   engine, metrics, optimize, robustness
  engine.py          TradingEngine              bot/data/       feed, history
  config.py          pydantic config + secrets  bot/execution/  paper, live, replay, executor, order_id
  models.py          Signal, Position, Order…   bot/notify/     Telegram / Discord
  common.py          time + retry helpers       bot/risk/       RiskManager
  logging_utils.py   JSON logging               bot/state/      SQLite store
  modes.py           safe/balanced/aggressive   bot/strategy/   base, indicators, ema_rsi, breakout,
  reports.py         analytics, CSV exports                     mean_reversion, regime, buy_hold, ai
config.yaml  .env.example  Dockerfile  docker-compose.yml  data/samples/  tests/
```
