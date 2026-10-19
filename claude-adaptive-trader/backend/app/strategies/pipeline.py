"""Candles -> features -> regimes -> strategy signals, the same way for backtests and paper trading."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.config import Settings
from app.regimes.detector import detect
from app.regimes.features import build_features
from app.strategies.base import Strategy, StrategyContext


@dataclass
class MarketView:
    pair: str
    timeframe: str
    features: pd.DataFrame
    regime: pd.DataFrame

    @property
    def ts(self) -> pd.Series:
        return self.features["ts"]


def prepare(candles: pd.DataFrame, pair: str, timeframe: str, confirm_timeframes: list[str],
            settings: Settings) -> MarketView:
    features = build_features(candles, timeframe, confirm_timeframes, settings.regime)
    regime = detect(features, settings.regime, confirm_timeframes)
    return MarketView(pair=pair, timeframe=timeframe, features=features, regime=regime)


def context(settings: Settings, timeframe: str, confirm_timeframes: list[str],
            cost_multiplier: float = 1.0) -> StrategyContext:
    return StrategyContext(round_trip_cost_pct=settings.costs.round_trip_pct(cost_multiplier),
                           signal_timeframe=timeframe, confirm_timeframes=tuple(confirm_timeframes))


def signals(view: MarketView, strategy: Strategy, ctx: StrategyContext) -> pd.DataFrame:
    return strategy.signals(view.features, view.regime, ctx)
