# What's new

Every entry is one released build. The app shows the newest entry after it updates itself.

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
