# What's new

Every entry is one released build. The app shows the newest entry after it updates itself.

## 0.9.0

**This release is about the win rate, and about what the win rate is worth.**

- **Entry filters.** A new `filters` section decides when *not* to enter, whatever the
  strategy says: confirm against a longer timeframe (daily while trading hourly), skip
  markets that are barely moving or moving too wildly, skip chosen hours, skip weekends.
  They apply to every strategy including the AI one, and they never block an exit.
- **Measured, not guessed.** On the two sample files, the daily trend filter plus skipping
  weekends took the breakout strategy from a 39.3% win rate to 48.4% (binance) and from
  37.6% to 44.0% (coinbase), roughly doubled the profit per trade, and cut the worst
  drawdown from -15.3% to -6.3%. Those two are on by default in Balanced because they were
  the only settings that improved *both* files. Everything that helped only one of them is
  available but off.
- **Time stop.** `exits.time_stop_candles` closes a position that has gone nowhere. On the
  binance sample it took the win rate from 39.3% to 50.0% and the drawdown from -8.0% to
  -5.6%; on the coinbase sample it raised the win rate but cost profit per trade, so it is
  off in Balanced and on in Safe and Aggressive.
- **Expectancy everywhere.** Every backtest now reports the money made per trade, the
  Research tab can rank by it, and it sits on the results tiles next to the win rate -
  because a 65% win rate with a small average win is a losing strategy, which is exactly
  what tightening the take profit produces.
- **A win-rate floor in the search.** The Research tab takes a minimum win rate: settings
  below it are discarded, while the objective still decides which survivor is best.
  A floor, not a goal.
- **Trading modes carry all of it.** Safe filters hardest and is patient; Aggressive keeps
  the weekend rule and a short time stop; each mode raises the candle history it needs.
- **Fixed: a filter that cannot be fed.** A daily trend line needs weeks of hourly candles.
  The live bot refuses to start rather than answering "not ready" forever, the backtester
  widens its own window, and a backtest over a range too short for the filters runs without
  them and says so in the report instead of failing.

## 0.8.0

- **Three new strategies.** `breakout` buys when price clears the high of the last N
  candles and leaves when it loses the recent low; `mean_reversion` buys sharp dips below
  the lower Bollinger band while the longer trend is still rising; `regime` measures how
  strongly the market is trending (ADX) and hands each trade to whichever of the two fits
  the current market, keeping the half that opened a position in charge of its exit.
- **A benchmark to beat.** The `buy_hold` strategy buys once and holds, so every backtest
  and walk-forward run can be compared against doing nothing at all.
- **Modes cover every strategy.** Safe, Balanced and Aggressive now carry their own
  settings for each strategy instead of pushing `ema_rsi`'s parameters into all of them.
- **The search knows each strategy.** The Research tab searches the parameters of the
  strategy you actually selected, and refuses to grid-search the AI strategy (it would
  call the API once per candle, per combination, and cost real money).
- **Is the result real? Robustness testing.** A third mode in the Research tab re-deals the
  same trades thousands of times and reports the spread you should actually expect: the
  median outcome, the bad fifth, the typical and the worst drawdown, how often the account
  ended down, and how often it lost half its value. It then sweeps every setting one at a
  time and says whether the profit sits on a wide plateau (a real edge) or on a single
  spike (a number fitted to the past).
- **Maker orders.** `exchange.order_type: limit` posts entries a little below the price
  instead of crossing the spread, paying the lower maker fee and no slippage. The catch is
  modelled honestly: an order the market never reaches does not fill, and that trade is
  missed. Exits always stay market orders, because an exit has to happen.
- **Fixed: the buy-and-hold benchmark never traded.** Position size comes from the distance
  to the stop, and the benchmark has no stop, so every one of its entries was refused and
  it reported a flat 0%. It now holds the market properly (182% against a theoretical 183%
  on the sample, with the 29% drawdown that holding really cost).
- **Fixed: a position sized to the whole account was unaffordable.** Sizing ignored
  slippage, so at a 100% position cap the exchange rejected the order for being a few
  dollars over the balance. Invisible at the default 25% cap, fatal at 100%.
- **Fixed: a manual order during a candle could fail or lose data.** The trading loop and
  the UI share one database connection, and using it from both at once made SQLite raise -
  the UI showed a 500, and orders, trades or equity rows could silently go unwritten. Every
  statement now runs under a lock, and a candle, a tick, a manual order and a reconcile are
  each one indivisible step.
- **Fixed: leftover settings no longer stop the bot.** Switching from the AI strategy back
  to a technical one used to leave `model` and `mode` in config.yaml, and the next Start
  died with "Extra inputs are not permitted". Those settings are now dropped, named in the
  log and removed from the file when you save Settings.
- **Fixed: updating over a locked BTCBot.old.exe.** If the previous executable was still
  held by Windows, the update failed with "[WinError 5] Access is denied". The old build is
  now parked under a free name, a half-finished swap is rolled back, and leftovers are
  cleaned up on the next start.

## 0.7.0

- **Parameter search and walk-forward testing.** A Research tab that tries hundreds of
  parameter sets and then tests the winners on data the search never saw. It ends with a
  plain verdict, including "do not trade this" when the result does not hold up.
- **History download.** Pull years of candles straight from the exchange into a local file
  and use them for backtests and searches.
- **Better exits.** A trailing stop, a move to breakeven once a trade is in profit, and a
  partial take profit that banks part of the position early. Plus a trend filter that only
  buys above a longer moving average.
- **Telegram control.** Send /status, /pnl, /position, /why, /stop, /start, /kill and
  /unkill from your phone. Only the chat you configured is obeyed.
- **Watchdog.** If the bot stops producing cycles while it is supposed to be trading, you
  get an alert instead of silence.
- **Analytics and exports.** Profit by month, expectancy per trade, results per exit type,
  streaks and trade durations, plus CSV export of every trade and the equity curve.
- **Faster backtests.** Indicators are computed once per run instead of once per candle,
  which makes a backtest about nine times faster with identical results.

## 0.6.1

- **Backtest dates.** Each data file now shows the period it covers, the date pickers are
  limited to that period, and a range with no candles explains which dates the file
  actually has instead of failing with "no candles in the requested range".
- **"Why no trades yet?"** A panel on the dashboard that answers the question directly: how
  many candles were evaluated, how often each reason blocked a trade, how far the EMAs are
  from crossing, and what to change to trade more often.

## 0.6.0

- **App icon and splash screen.** The executable carries a proper Bitcoin icon, and a
  splash screen with a progress bar appears while Windows unpacks the app, so the wait no
  longer looks like nothing is happening.
- **License keys.** Live trading now needs a key (`BTCB-XXXXX-XXXXX-XXXXX-XXXXX`), entered
  once under Settings. Paper trading, backtesting, the charts and the AI trader keep
  working without one, so the bot can be evaluated safely before buying.
- **What's new.** After an update the app shows exactly what changed in the version you
  just received; you can reopen it any time from the footer.
- **One-click update.** Pressing Update downloads, verifies and installs the new build and
  restarts the app by itself. No confirmation step, no manual restart.
- **Loading screen.** The dashboard shows an animated boot screen while it connects, with
  the steps it is working through instead of an empty page.

## 0.5.0

- **AI trader.** Claude reads the last candles, the indicators, your position and the risk
  state and returns a decision with a stop, a target and a one-sentence reason. It never
  sizes or places an order: the risk manager still does that, so the daily loss limit,
  cooldowns and the kill switch always apply.
- **Manual trading.** A Trade tab to buy and sell by hand, with cash presets, stop and
  target presets, a plain-language preview of what the order risks, and one-click exit.
- **Three trading modes.** Safe, Balanced and Aggressive rewrite the risk limits and the
  strategy parameters in one click, with a table showing exactly what differs.
- **Log and Debug channels.** Log shows what happened (orders, trades, halts, errors);
  Debug shows every cycle with a text filter and a copy button.
- **New interface.** Four themes, motion controls, toast notifications for fills and risk
  events, and a confidence meter on the last decision.

## 0.4.0

- **Self-updates.** Each published build is detected automatically and can install itself
  while the bot is stopped. Downloads are verified against the release checksums.

## 0.3.0

- **Readable settings.** Every setting has a plain-language label and help text, with
  dropdowns for the timeframe, strategy and log level.
- **Timeframe selection.** The bot's timeframe is a setting; the chart has its own
  timeframe buttons from 1m to 1d.
- **Temporary-folder warning.** The app warns when it is run from an extraction folder
  where its settings and keys would be lost.

## 0.2.0

- **Desktop app.** A window with a dashboard, equity curve, trade history, backtests,
  settings and a log, packaged as a Windows executable.
- **Price chart.** Candlesticks from the exchange with EMA lines, volume, entry and exit
  markers, the position's stop and target, and a live stream from Binance.

## 0.1.0

- First version: the trading engine, the EMA/RSI strategy, the risk manager, paper
  trading, the backtester and the command line interface.
