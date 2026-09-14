"""Trading modes: safe, balanced, aggressive.

A mode is a named bundle of risk limits and strategy parameters. Selecting one overwrites
exactly the keys it defines in config.yaml; anything else the user set is kept. The mode is
a starting point, not a promise: "aggressive" means larger size and looser filters, which
means larger losses too.
"""
from __future__ import annotations

from typing import Any

MODES: dict[str, dict[str, Any]] = {
    "safe": {
        "label": "Safe",
        "summary": "Small size, wide stops, few trades. Halts early after losses.",
        "detail": "Risks 0.5% of equity per trade, caps a position at 10% of equity and stops "
                  "trading for the day after a 2% loss. Only buys with clear momentum.",
        "risk": {
            "risk_per_trade_pct": 0.5, "max_position_pct": 10.0, "max_daily_loss_pct": 2.0,
            "max_consecutive_losses": 2, "cooldown_minutes": 720.0, "min_seconds_between_trades": 14400.0,
            "min_confidence": 0.7,
        },
        "strategy_params": {
            "ema_fast": 12, "ema_slow": 26, "rsi_buy_min": 50.0, "rsi_buy_max": 65.0,
            "atr_stop_mult": 3.0, "atr_tp_mult": 4.5, "trend_lookback": 10,
        },
        "ai": {"effort": "high", "min_confidence": 0.7, "max_stop_atr": 3.0, "only_on_technical_setup": True},
        "params_by_strategy": {
            "breakout": {"entry_period": 40, "exit_period": 20, "atr_stop_mult": 3.0,
                         "min_breakout_atr": 0.5, "trend_filter_period": 100},
            "mean_reversion": {"bb_period": 20, "bb_std": 2.5, "rsi_buy_max": 30.0, "atr_stop_mult": 3.0,
                               "trend_filter_period": 200, "max_stretch_atr": 1.5, "exit_band": "middle"},
            "regime": {"trend_above": 27.0, "range_below": 18.0,
                       "trend_params": {"entry_period": 40, "exit_period": 20, "atr_stop_mult": 3.0,
                                        "min_breakout_atr": 0.5, "trend_filter_period": 100},
                       "range_params": {"bb_period": 20, "bb_std": 2.5, "rsi_buy_max": 30.0,
                                        "atr_stop_mult": 3.0, "trend_filter_period": 200,
                                        "max_stretch_atr": 1.5}},
            "buy_hold": {"stop_loss_pct": 25.0},
        },
    },
    "balanced": {
        "label": "Balanced",
        "summary": "The default. Moderate size, normal trend-following discipline.",
        "detail": "Risks 1% of equity per trade, caps a position at 25% of equity and stops "
                  "trading for the day after a 3% loss.",
        "risk": {
            "risk_per_trade_pct": 1.0, "max_position_pct": 25.0, "max_daily_loss_pct": 3.0,
            "max_consecutive_losses": 3, "cooldown_minutes": 240.0, "min_seconds_between_trades": 3600.0,
            "min_confidence": 0.5,
        },
        "strategy_params": {
            "ema_fast": 12, "ema_slow": 26, "rsi_buy_min": 45.0, "rsi_buy_max": 70.0,
            "atr_stop_mult": 2.0, "atr_tp_mult": 3.0, "trend_lookback": 5,
        },
        "ai": {"effort": "medium", "min_confidence": 0.5, "max_stop_atr": 3.0, "only_on_technical_setup": False},
        "params_by_strategy": {
            "breakout": {"entry_period": 20, "exit_period": 10, "atr_stop_mult": 2.0,
                         "min_breakout_atr": 0.25, "trend_filter_period": 100},
            "mean_reversion": {"bb_period": 20, "bb_std": 2.0, "rsi_buy_max": 35.0, "atr_stop_mult": 2.5,
                               "trend_filter_period": 200, "max_stretch_atr": 2.0, "exit_band": "middle"},
            "regime": {"trend_above": 25.0, "range_below": 20.0,
                       "trend_params": {"entry_period": 20, "exit_period": 10, "atr_stop_mult": 2.0,
                                        "min_breakout_atr": 0.25, "trend_filter_period": 100},
                       "range_params": {"bb_period": 20, "bb_std": 2.0, "rsi_buy_max": 35.0,
                                        "atr_stop_mult": 2.5, "trend_filter_period": 200,
                                        "max_stretch_atr": 2.0}},
            "buy_hold": {"stop_loss_pct": 35.0},
        },
    },
    "aggressive": {
        "label": "Aggressive",
        "summary": "Larger size, tighter stops, many more trades. Expect deep drawdowns.",
        "detail": "Risks 2% of equity per trade, caps a position at 50% of equity and only "
                  "stops after a 6% daily loss. Enters on earlier, weaker signals.",
        "risk": {
            "risk_per_trade_pct": 2.0, "max_position_pct": 50.0, "max_daily_loss_pct": 6.0,
            "max_consecutive_losses": 5, "cooldown_minutes": 60.0, "min_seconds_between_trades": 900.0,
            "min_confidence": 0.35,
        },
        "strategy_params": {
            "ema_fast": 8, "ema_slow": 21, "rsi_buy_min": 40.0, "rsi_buy_max": 78.0,
            "atr_stop_mult": 1.5, "atr_tp_mult": 2.5, "trend_lookback": 3,
        },
        "ai": {"effort": "medium", "min_confidence": 0.35, "max_stop_atr": 2.0, "only_on_technical_setup": False},
        "params_by_strategy": {
            "breakout": {"entry_period": 10, "exit_period": 5, "atr_stop_mult": 1.5,
                         "min_breakout_atr": 0.0, "trend_filter_period": 0},
            "mean_reversion": {"bb_period": 14, "bb_std": 1.8, "rsi_buy_max": 45.0, "atr_stop_mult": 2.0,
                               "trend_filter_period": 100, "max_stretch_atr": 0.0, "exit_band": "middle"},
            "regime": {"trend_above": 22.0, "range_below": 22.0,
                       "trend_params": {"entry_period": 10, "exit_period": 5, "atr_stop_mult": 1.5,
                                        "min_breakout_atr": 0.0, "trend_filter_period": 0},
                       "range_params": {"bb_period": 14, "bb_std": 1.8, "rsi_buy_max": 45.0,
                                        "atr_stop_mult": 2.0, "trend_filter_period": 100,
                                        "max_stretch_atr": 0.0}},
            "buy_hold": {"stop_loss_pct": 0.0},
        },
    },
}

DEFAULT_MODE = "balanced"


def mode_list() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"], "summary": v["summary"], "detail": v["detail"],
             "risk": v["risk"]} for k, v in MODES.items()]


def mode_params_for(mode: str, strategy: str) -> dict[str, Any]:
    """The parameters a mode sets for one strategy.

    Every strategy has its own parameter names and rejects unknown ones, so a mode must
    never push `ema_rsi`'s settings into, say, the breakout strategy. A strategy the mode
    knows nothing about keeps whatever parameters the user configured.
    """
    preset = MODES[mode]
    if strategy in ("ema_rsi", "ai"):
        return dict(preset["strategy_params"])
    return {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in preset.get("params_by_strategy", {}).get(strategy, {}).items()}


def _merge_params(current: dict[str, Any], preset: dict[str, Any]) -> dict[str, Any]:
    """Overwrite exactly the keys the mode defines, one level into nested parameter groups."""
    out = {**current}
    for key, value in preset.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def apply_mode(config: dict[str, Any], mode: str) -> dict[str, Any]:
    """Return a copy of a config dict with the mode's risk and strategy settings applied."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of {sorted(MODES)}")
    preset = MODES[mode]
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in config.items()}
    out["risk"] = {**out.get("risk", {}), **preset["risk"]}
    strategy = {**out.get("strategy", {})}
    params = {**strategy.get("params", {})}
    name = strategy.get("name", "ema_rsi")
    if name == "ai":
        params.update({k: v for k, v in preset["ai"].items()})
        params["mode"] = mode
        inner = {**params.get("indicator_params", {}), **preset["strategy_params"]}
        params["indicator_params"] = inner
    else:
        params = _merge_params(params, mode_params_for(mode, name))
    strategy["params"] = params
    out["strategy"] = strategy
    out["mode"] = mode
    return out


def detect_mode(config: dict[str, Any]) -> str | None:
    """Which mode a config matches on its risk numbers, or None when it was hand-tuned."""
    risk = config.get("risk") or {}
    for name, preset in MODES.items():
        if all(abs(float(risk.get(k, -1)) - float(v)) < 1e-9 for k, v in preset["risk"].items()):
            return name
    return None
