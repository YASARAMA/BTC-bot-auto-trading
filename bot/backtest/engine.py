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

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    trades: list[Trade]
    equity: pd.DataFrame
    strategy: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)


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
        initial_cash=cash, clock=lambda: clock["now"],
    )
    store = StateStore(":memory:")
    risk_cfg = cfg.risk.model_copy(update={"kill_switch_file": ""})  # a stray file must not alter a backtest
    risk = RiskManager(risk_cfg, fee_rate=cfg.exchange.fee_rate)
    executor = OrderExecutor(
        exchange, store, symbol=symbol, strategy_name=strategy.name,
        order_timeout_seconds=cfg.exchange.order_timeout_seconds, order_poll_seconds=0.0,
        sleep=lambda _s: None, clock=lambda: clock["now"], quiet=True,
    )
    engine = TradingEngine(
        cfg=cfg, strategy=strategy, risk=risk, exchange=exchange, executor=executor, store=store,
        notifier=Notifier(cfg.notify), clock=lambda: clock["now"], mode="backtest",
    )

    strategy.precompute(df)
    n = len(df)
    window = cfg.exchange.candle_history
    warm = strategy.warmup
    if n < warm + 2:
        raise ValueError(f"need at least {warm + 2} candles for {name}, got {n}")
    first_price = float(df["close"].iloc[warm - 1])
    for i in range(warm - 1, n):
        row = df.iloc[i]
        clock["now"] = int(row["ts"]) + tf_ms
        next_open = float(df["open"].iloc[i + 1]) if i + 1 < n else float(row["close"])
        exchange.set_price(next_open)
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
        metrics=metrics, trades=trades, equity=equity, strategy=strategy.describe(),
        config={"symbol": symbol, "timeframe": cfg.exchange.timeframe, "fee_rate": cfg.exchange.fee_rate,
                "slippage_bps": cfg.exchange.slippage_bps, "risk": risk_cfg.model_dump()},
    )
