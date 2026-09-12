from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bot.config import BotConfig, ExchangeConfig, RiskConfig, StrategyConfig
from bot.models import Action, Signal
from bot.strategy.base import Strategy

TF_MS = 3_600_000
T0 = 1_700_000_000_000  # 2023-11-14T22:13:20Z, an arbitrary fixed origin


def make_ohlcv(closes, start_ts: int = T0, tf_ms: int = TF_MS, spread: float = 0.005) -> pd.DataFrame:
    closes = np.asarray(list(closes), dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    start = start_ts - (start_ts % tf_ms)
    ts = start + np.arange(len(closes)) * tf_ms
    return pd.DataFrame({"ts": ts.astype("int64"), "open": opens, "high": highs, "low": lows,
                         "close": closes, "volume": np.full(len(closes), 10.0)})


def trending_series(n_down: int = 120, n_up: int = 120, n_down2: int = 120, start: float = 100.0):
    """Down, then up, then down again: guarantees an EMA cross up and a later cross down."""
    out = [start]
    for _ in range(n_down):
        out.append(out[-1] * 0.998)
    for i in range(n_up):
        out.append(out[-1] * (1.004 + 0.001 * math.sin(i / 3)))
    for _ in range(n_down2):
        out.append(out[-1] * 0.996)
    return out


@pytest.fixture
def cfg(tmp_path) -> BotConfig:
    return BotConfig(
        exchange=ExchangeConfig(symbol="BTC/USDT", timeframe="1h", fee_rate=0.001, slippage_bps=5.0,
                                candle_history=300, order_timeout_seconds=1.0, order_poll_seconds=0.0),
        strategy=StrategyConfig(name="ema_rsi", params={}),
        risk=RiskConfig(kill_switch_file=str(tmp_path / "KILL_SWITCH"), min_seconds_between_trades=0.0,
                        cooldown_minutes=60.0),
    )


class ScriptedStrategy(Strategy):
    """Returns a scripted Signal for given candle timestamps, HOLD otherwise."""

    name = "scripted"

    def __init__(self, script: dict[int, Signal] | None = None, warmup: int = 1) -> None:
        super().__init__({})
        self.script = script or {}
        self._warmup = warmup
        self.calls = 0

    @property
    def warmup(self) -> int:
        return self._warmup

    def on_candle(self, df: pd.DataFrame) -> Signal:
        self.calls += 1
        ts = int(df["ts"].iloc[-1])
        return self.script.get(ts, Signal.hold("scripted hold"))


def buy_signal(close: float, stop_pct: float = 0.02, tp_pct: float = 0.04, confidence: float = 1.0) -> Signal:
    return Signal(Action.BUY, confidence, "scripted buy", stop_loss=close * (1 - stop_pct), take_profit=close * (1 + tp_pct))
