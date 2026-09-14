# What's new

Every entry is one released build. The app shows the newest entry after it updates itself.

## 0.8.0

- **Three new strategies.** `breakout` buys when price clears the high of the last N
  candles and leaves when it loses the recent low; `mean_reversion` buys sharp dips below
  the lower Bollinger band while the longer trend is still rising; `regime` measures how
  strongly the market is trending (ADX) and hands each trade to whichever of the two fits
  the current market, keeping the half that opened a position in charge of its exit.
- **A benchmark to beat.** The `buy_hold` strategy buys once and holds, so every backtest
  and walk-forward run can be compared against doing nothing at all.
- **Modes cover every strategy.** Safe, Balanced and Aggressive now carry their own
  settings for each strategy instead of pushing `ema_rsi`\'s parameters into all of them.
- **The search knows each strategy.** The Research tab searches the parameters of the
  strategy you actually selected, and refuses to grid-search the AI strategy (it would
  call the API once per candle, per combination, and cost real money).
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
