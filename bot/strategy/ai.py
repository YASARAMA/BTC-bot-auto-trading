"""AI strategy: Claude decides BUY / SELL / HOLD from a structured market snapshot.

The model never places orders. It returns one JSON object that becomes a Signal, which
goes through the same RiskManager as any other strategy: position sizing, daily-loss
halts, cooldowns and the kill switch all still apply.

Without an API key (or when the call fails) the strategy falls back to the technical
`ema_rsi` signal, so the bot keeps working rather than stopping.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.models import Action, Signal
from bot.strategy.base import Strategy, validate_frame
from bot.strategy.ema_rsi import EmaRsiParams, EmaRsiStrategy

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """You are the signal engine of an automated BTC spot trading bot.

You receive a market snapshot and return exactly one decision. You never place orders and
you never choose position size: a risk manager does that, and it can reject your signal.

Rules:
1. BUY only when there is no open position. SELL only when there is one. Otherwise HOLD.
2. For a BUY you must give a stop_loss below the current close, derived from ATR, never
   wider than max_stop_atr multiples of ATR, and a take_profit above the close.
3. Prefer HOLD when signals conflict, when the trend is unclear, or when volatility is so
   low that a sensible stop cannot be placed. A missed trade costs nothing; a bad one costs money.
4. Never invent news, sentiment, order-flow or any data you were not given.
5. reason must be one sentence citing the specific indicator values that drove the decision.
6. confidence is your honest probability that this decision is correct, from 0 to 1.

The trading mode tells you how much risk the operator accepts. Respect it:
- safe: only high-conviction entries in a clear uptrend, wide stops, few trades.
- balanced: normal trend-following discipline.
- aggressive: act on earlier momentum signals, tighter stops, more trades accepted."""

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "stop_loss": {"type": ["number", "null"]},
        "take_profit": {"type": ["number", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "confidence", "stop_loss", "take_profit", "reason"],
    "additionalProperties": False,
}


class AiParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(DEFAULT_MODEL, description="Claude model id")
    effort: str = Field("medium", description="low | medium | high | xhigh | max")
    max_tokens: int = Field(2000, ge=256)
    timeout_seconds: float = Field(60.0, gt=0)
    candles_in_prompt: int = Field(60, ge=10, le=200)
    max_stop_atr: float = Field(3.0, gt=0)
    min_confidence: float = Field(0.0, ge=0.0, le=1.0, description="below this the signal becomes HOLD")
    fallback_to_technical: bool = Field(True, description="use ema_rsi when the API is unavailable")
    only_on_technical_setup: bool = Field(
        False, description="call the model only when ema_rsi is not HOLD (saves API cost)")
    mode: str = Field("balanced", description="safe | balanced | aggressive")
    indicator_params: dict[str, Any] = Field(default_factory=dict, description="ema_rsi params for the snapshot")

    @model_validator(mode="after")
    def _check(self) -> "AiParams":
        if self.effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("effort must be low, medium, high, xhigh or max")
        if self.mode not in ("safe", "balanced", "aggressive"):
            raise ValueError("mode must be safe, balanced or aggressive")
        return self


class AiStrategy(Strategy):
    """Claude-driven strategy. Set ANTHROPIC_API_KEY to enable it."""

    name = "ai"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.p = AiParams.model_validate(self.raw_params)
        self.technical = EmaRsiStrategy(self.p.indicator_params)
        self._client: Any = None
        self.last_call: dict[str, Any] = {}
        self.calls = 0
        self.failures = 0

    @property
    def warmup(self) -> int:
        return self.technical.warmup

    # ----- client ---------------------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        return key.strip() if key and key.strip() else None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.p.timeout_seconds, max_retries=1)
        return self._client

    # ----- snapshot -------------------------------------------------------------------
    def snapshot(self, df: pd.DataFrame, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ind = self.technical.indicators(df)
        last = df.iloc[-1]
        li = ind.iloc[-1]
        tail = df.tail(self.p.candles_in_prompt)
        closes = tail["close"]
        vol_mean = float(df["volume"].tail(min(len(df), 720)).mean() or 0.0)

        def num(v: Any) -> float | None:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if f != f else round(f, 6)

        return {
            "symbol": (context or {}).get("symbol", "BTC/USDT"),
            "timeframe": (context or {}).get("timeframe", "?"),
            "mode": self.p.mode,
            "max_stop_atr": self.p.max_stop_atr,
            "candle_time": (context or {}).get("candle_time"),
            "close": num(last["close"]),
            "indicators": {
                "ema_fast": num(li.get("ema_fast")), "ema_slow": num(li.get("ema_slow")),
                "rsi": num(li.get("rsi")), "atr": num(li.get("atr")),
                "ema_fast_prev": num(ind["ema_fast"].iloc[-2]) if len(ind) > 1 else None,
                "ema_slow_prev": num(ind["ema_slow"].iloc[-2]) if len(ind) > 1 else None,
            },
            "stats": {
                "change_pct_24": num((float(last["close"]) / float(df["close"].iloc[-25]) - 1) * 100) if len(df) > 25 else None,
                "high_recent": num(tail["high"].max()), "low_recent": num(tail["low"].min()),
                "volume_vs_average": num(float(last["volume"]) / vol_mean) if vol_mean else None,
                "range_pct": num((float(tail["high"].max()) / float(tail["low"].min()) - 1) * 100),
            },
            "candles": [
                [int(r.ts), num(r.open), num(r.high), num(r.low), num(r.close), num(r.volume)]
                for r in tail.itertuples()
            ],
            "position": (context or {}).get("position"),
            "risk": (context or {}).get("risk"),
        }

    # ----- decision -------------------------------------------------------------------
    def on_candle(self, df: pd.DataFrame, context: dict[str, Any] | None = None) -> Signal:
        validate_frame(df)
        if len(df) < self.warmup:
            return Signal.hold(f"warming up ({len(df)}/{self.warmup} candles)")
        technical = self.technical.on_candle(df)
        if self.p.only_on_technical_setup and technical.action == Action.HOLD:
            return Signal.hold(f"AI not called (no technical setup); {technical.reason}")
        if not self.available:
            if self.p.fallback_to_technical:
                return Signal(technical.action, technical.confidence,
                              f"AI key missing, using technical signal: {technical.reason}",
                              technical.stop_loss, technical.take_profit)
            return Signal.hold("AI strategy has no ANTHROPIC_API_KEY")

        snapshot = self.snapshot(df, context)
        started = time.monotonic()
        try:
            decision = self.ask(snapshot)
            self.calls += 1
        except Exception as exc:  # noqa: BLE001 - the loop must survive a failed API call
            self.failures += 1
            self.last_call = {"error": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - started, 2)}
            log.warning("AI decision failed: %s", self.last_call["error"])
            if self.p.fallback_to_technical:
                return Signal(technical.action, technical.confidence,
                              f"AI unavailable ({type(exc).__name__}), using technical signal: {technical.reason}",
                              technical.stop_loss, technical.take_profit)
            return Signal.hold(f"AI unavailable: {type(exc).__name__}")

        self.last_call = {
            "seconds": round(time.monotonic() - started, 2), "model": self.p.model,
            "decision": decision, "technical": technical.to_dict(),
            "usage": self.last_call.get("usage"), "snapshot_close": snapshot["close"],
        }
        return self.to_signal(decision, snapshot, technical)

    def ask(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """One Claude call returning the decision object. Raises on API failure."""
        client = self.client()
        response = client.messages.create(
            model=self.p.model,
            max_tokens=self.p.max_tokens,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": self.p.effort, "format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(snapshot, separators=(",", ":"))}],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(f"model declined the request ({getattr(details, 'category', None)})")
        usage = getattr(response, "usage", None)
        self.last_call = {"usage": {"input": getattr(usage, "input_tokens", None),
                                    "output": getattr(usage, "output_tokens", None)} if usage else None}
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise ValueError("model returned no text block")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("model returned a non-object decision")
        return data

    def to_signal(self, decision: dict[str, Any], snapshot: dict[str, Any], technical: Signal) -> Signal:
        """Validate the model's decision. Anything unusable becomes HOLD."""
        action_text = str(decision.get("action", "HOLD")).upper()
        if action_text not in ("BUY", "SELL", "HOLD"):
            return Signal.hold(f"AI returned an unknown action {action_text!r}")
        action = Action(action_text)
        try:
            confidence = max(0.0, min(1.0, float(decision.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        reason = str(decision.get("reason") or "").strip()[:500] or "no reason given"
        prefix = f"AI({self.p.mode})"
        if action == Action.HOLD:
            return Signal.hold(f"{prefix}: {reason}")
        if confidence < self.p.min_confidence:
            return Signal.hold(f"{prefix}: confidence {confidence:.2f} below {self.p.min_confidence:.2f}; {reason}")
        if action == Action.SELL:
            return Signal(Action.SELL, confidence, f"{prefix}: {reason}")

        close = float(snapshot["close"] or 0.0)
        atr = float(snapshot["indicators"].get("atr") or 0.0)
        stop = decision.get("stop_loss")
        take = decision.get("take_profit")
        try:
            stop = float(stop) if stop is not None else None
            take = float(take) if take is not None else None
        except (TypeError, ValueError):
            return Signal.hold(f"{prefix}: unusable stop/target values; {reason}")
        if stop is None or not (0 < stop < close):
            return Signal.hold(f"{prefix}: BUY without a valid stop below {close}; {reason}")
        if atr > 0 and (close - stop) > self.p.max_stop_atr * atr:
            widest = close - self.p.max_stop_atr * atr
            reason = f"{reason} [stop tightened from {stop:.2f} to the {self.p.max_stop_atr:g}×ATR limit]"
            stop = widest
        if take is not None and take <= close:
            take = None
            reason = f"{reason} [take profit dropped: it was not above the price]"
        return Signal(Action.BUY, confidence, f"{prefix}: {reason}", stop_loss=stop, take_profit=take)

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "available": self.available, "calls": self.calls, "failures": self.failures}
