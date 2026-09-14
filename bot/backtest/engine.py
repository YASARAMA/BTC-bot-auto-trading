"""Backtester: drives the same TradingEngine, RiskManager and PaperExchange as live
trading over historical candles. Signals are computed on a closed candle and filled
at the next candle's open; stops and take-profits fill at their level (or at the open
when the candle gaps through them), with the configured slippage and fees."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from bot.backtest.metrics import compute_metrics
from bot.common import timeframe_to_ms
from bot.config import BotConfig
from bot.engine import TradingEngine
from bot.execution.executor import OrderExecutor
from bot.execution.paper import PaperExchange
from bot.models import OrderIntent, Side, Trade
from bot.notify.notifier import Notifier
from bot.risk.manager import RiskManager
from bot.state.store import StateStore
from bot.strategy import get_strategy
from bot.strategy.base import validate_frame
from bot.strategy.filters import EntryFilters

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    trades: list[Trade]
    equity: pd.DataFrame
    strategy: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # anything the run had to change to be possible


def run_backtest(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    strategy_name: str | None = None,
    params: dict[str, Any] | None = None,
    initial_cash: float | None = None,
    liquidate_at_end: bool = True,
) -> BacktestResult:
    validate_frame(df)
    df = df.sort_values("ts").reset_index(drop=True)
    name = strategy_name or cfg.strategy.name
    strategy = get_strategy(name, params if params is not None else cfg.strategy.params)
    tf_ms = timeframe_to_ms(cfg.exchange.timeframe)
    cash = initial_cash if initial_cash is not None else cfg.paper.initial_cash
    symbol = cfg.exchange.symbol

    clock = {"now": int(df["ts"].iloc[0])}
    exchange = PaperExchange(
        symbol=symbol, fee_rate=cfg.exchange.fee_rate, slippage_bps=cfg.exchange.slippage_bps,
        maker_fee_rate=cfg.exchange.maker_fee_rate, initial_cash=cash, clock=lambda: clock["now"],
    )
    store = StateStore(":memory:")
    risk_cfg = cfg.risk.model_copy(update={"kill_switch_file": ""})  # a stray file must not alter a backtest
    filters_cfg = cfg.filters
    if name == "buy_hold":
        # The benchmark is not a risk-managed strategy: it is the answer to "what if I had
        # just bought and held", so it uses the whole account and needs no stop. Sizing it
        # by risk-per-trade would compare the strategy against a token position instead.
        risk_cfg = risk_cfg.model_copy(update={
            "allow_entry_without_stop": True, "max_position_pct": 100.0,
            "min_seconds_between_trades": 0.0, "min_confidence": 0.0,
        })
        # Entry filters are off for it too: waiting for a rising daily trend before buying
        # is a strategy, and then the benchmark is no longer the thing it is there to be.
        filters_cfg = type(cfg.filters)()
    risk = RiskManager(risk_cfg, fee_rate=cfg.exchange.fee_rate, slippage_bps=cfg.exchange.slippage_bps)
    executor = OrderExecutor(
        exchange, store, symbol=symbol, strategy_name=strategy.name,
        order_timeout_seconds=cfg.exchange.order_timeout_seconds, order_poll_seconds=0.0,
        sleep=lambda _s: None, clock=lambda: clock["now"], quiet=True,
        order_type=cfg.exchange.order_type, limit_offset_bps=cfg.exchange.limit_offset_bps,
        limit_fallback_market=cfg.exchange.limit_fallback_market,
        wait_for_fill=False,  # the candle the order is placed in already decides its fate
    )
    engine = TradingEngine(
        cfg=cfg.model_copy(update={"filters": filters_cfg}), strategy=strategy, risk=risk, exchange=exchange,
        executor=executor, store=store,
        notifier=Notifier(cfg.notify), clock=lambda: clock["now"], mode="backtest",
    )

    strategy.precompute(df)
    n = len(df)
    notes: list[str] = []
    # The entry filters read history of their own - a daily trend line needs weeks of hourly
    # candles - so the rolling window has to be at least as long as the longest of them, or
    # the filter silently answers "not ready" on every candle and the run makes no trades.
    if engine.filters.active and n < engine.filters.warmup + 2:
        # Refusing the whole run would be worse: the filters are a default the user did not
        # ask for on this range. They are switched off and the result says so, because a
        # backtest whose settings quietly differ from the live bot's is not worth having.
        notes.append(
            f"entry filters were off for this run: they need {engine.filters.warmup + 2} candles "
            f"and this range has {n}. The live bot keeps them; a longer range would too.")
        engine.filters = EntryFilters()
    warm = max(strategy.warmup, engine.filters.warmup)
    window = max(cfg.exchange.candle_history, warm + 2)
    if n < warm + 2:
        raise ValueError(f"need at least {warm + 2} candles for {name}, got {n}")
    first_price = float(df["close"].iloc[warm - 1])
    for i in range(warm - 1, n):
        row = df.iloc[i]
        clock["now"] = int(row["ts"]) + tf_ms
        next_open = float(df["open"].iloc[i + 1]) if i + 1 < n else float(row["close"])
        exchange.set_price(next_open)
        # A limit order placed on this signal rests through the next candle: it fills only
        # if that candle's range reaches it, and is cancelled otherwise.
        if i + 1 < n:
            exchange.set_fill_window(float(df["high"].iloc[i + 1]), float(df["low"].iloc[i + 1]))
        else:
            exchange.set_fill_window(None, None)
        frame = df.iloc[max(0, i - window + 1) : i + 1]
        engine.process_candle(frame, fill_price=next_open, exit_at_level=True)

    last_price = float(df["close"].iloc[-1])
    if liquidate_at_end and engine.position is not None:
        ts = int(df["ts"].iloc[-1])
        intent = OrderIntent(side=Side.SELL, qty=engine.position.qty, ref_price=last_price, kind="exit",
                             candle_ts=ts + tf_ms, reason="end of data")
        order = executor.execute(intent)
        if order is not None:
            engine._apply_fill(order, intent, clock["now"], last_price)
            acct = engine.account(last_price)
            store.save_equity(ts + 1, acct.equity, acct.cash, 0.0, last_price)

    strategy.clear_precomputed()
    equity = pd.DataFrame(store.equity_curve())
    trades = store.trades()
    metrics = compute_metrics(equity, trades, tf_ms=tf_ms, initial_cash=cash, first_price=first_price, last_price=last_price)
    store.close()
    return BacktestResult(
        metrics=metrics, trades=trades, equity=equity, strategy=strategy.describe(), notes=notes,
        config={"symbol": symbol, "timeframe": cfg.exchange.timeframe, "fee_rate": cfg.exchange.fee_rate,
                "slippage_bps": cfg.exchange.slippage_bps, "risk": risk_cfg.model_dump()},
    )
