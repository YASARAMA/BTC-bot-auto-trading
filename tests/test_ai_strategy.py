import json

import pytest

from bot.models import Action, Signal
from bot.strategy import get_strategy
from bot.strategy.ai import AiStrategy
from tests.conftest import make_ohlcv, trending_series


class FakeResponse:
    def __init__(self, payload, stop_reason="end_turn", category=None):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.stop_details = type("D", (), {"category": category})() if category else None
        self.usage = type("U", (), {"input_tokens": 1200, "output_tokens": 90})()


class FakeClient:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

        class Messages:
            def create(inner, **kwargs):  # noqa: N805
                self.calls.append(kwargs)
                if self.error:
                    raise self.error
                return self.payload

        self.messages = Messages()


def ai(monkeypatch, payload=None, error=None, **params):
    s = AiStrategy({"indicator_params": {"rsi_buy_min": 0, "rsi_buy_max": 100}, **params})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    s._client = FakeClient(payload, error)
    return s


def frame():
    return make_ohlcv(trending_series(), spread=0.003)


def test_without_key_falls_back_to_technical(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = AiStrategy({"indicator_params": {"rsi_buy_min": 0, "rsi_buy_max": 100}})
    assert not s.available
    sig = s.on_candle(frame())
    assert "AI key missing" in sig.reason
    strict = AiStrategy({"fallback_to_technical": False})
    assert strict.on_candle(frame()).action == Action.HOLD


def test_buy_decision_becomes_a_signal(monkeypatch):
    df = frame()
    close = float(df["close"].iloc[-1])
    s = ai(monkeypatch, FakeResponse({"action": "BUY", "confidence": 0.8, "stop_loss": close * 0.98,
                                      "take_profit": close * 1.04, "reason": "EMA cross with RSI 58"}))
    sig = s.on_candle(df, context={"symbol": "BTC/USDT", "timeframe": "1h"})
    assert sig.action == Action.BUY and sig.confidence == 0.8
    assert sig.stop_loss == pytest.approx(close * 0.98) and sig.take_profit == pytest.approx(close * 1.04)
    assert sig.reason.startswith("AI(balanced):") and "RSI 58" in sig.reason
    sent = s._client.calls[0]
    assert sent["model"] == "claude-opus-5" and sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["format"]["type"] == "json_schema"
    snapshot = json.loads(sent["messages"][0]["content"])
    assert snapshot["symbol"] == "BTC/USDT" and snapshot["mode"] == "balanced"
    assert len(snapshot["candles"]) == 60 and snapshot["indicators"]["rsi"] is not None
    assert s.last_call["usage"] == {"input": 1200, "output": 90} and s.calls == 1


def test_unsafe_decisions_are_rejected_or_clamped(monkeypatch):
    df = frame()
    close = float(df["close"].iloc[-1])
    # stop above the price -> HOLD
    s = ai(monkeypatch, FakeResponse({"action": "BUY", "confidence": 0.9, "stop_loss": close * 1.1,
                                      "take_profit": close * 1.2, "reason": "bad stop"}))
    assert s.on_candle(df).action == Action.HOLD
    # stop wider than max_stop_atr -> tightened, not refused
    s = ai(monkeypatch, FakeResponse({"action": "BUY", "confidence": 0.9, "stop_loss": close * 0.5,
                                      "take_profit": close * 1.2, "reason": "very wide stop"}), max_stop_atr=2.0)
    sig = s.on_candle(df)
    assert sig.action == Action.BUY and sig.stop_loss > close * 0.5 and "tightened" in sig.reason
    # take profit below price -> dropped
    s = ai(monkeypatch, FakeResponse({"action": "BUY", "confidence": 0.9, "stop_loss": close * 0.98,
                                      "take_profit": close * 0.9, "reason": "bad target"}))
    sig = s.on_candle(df)
    assert sig.action == Action.BUY and sig.take_profit is None and "take profit dropped" in sig.reason
    # nonsense action, and confidence below the floor
    assert ai(monkeypatch, FakeResponse({"action": "MOON", "confidence": 1, "stop_loss": None,
                                         "take_profit": None, "reason": "x"})).on_candle(df).action == Action.HOLD
    low = ai(monkeypatch, FakeResponse({"action": "BUY", "confidence": 0.2, "stop_loss": close * 0.98,
                                        "take_profit": close * 1.05, "reason": "weak"}), min_confidence=0.6)
    assert low.on_candle(df).action == Action.HOLD


def test_api_failure_and_refusal_fall_back(monkeypatch):
    df = frame()
    s = ai(monkeypatch, error=ConnectionError("no network"))
    sig = s.on_candle(df)
    assert "AI unavailable" in sig.reason and s.failures == 1 and "no network" in s.last_call["error"]
    refused = ai(monkeypatch, FakeResponse({"action": "BUY"}, stop_reason="refusal", category="cyber"))
    assert "AI unavailable" in refused.on_candle(df).reason
    strict = ai(monkeypatch, error=ConnectionError("down"), fallback_to_technical=False)
    assert strict.on_candle(df).action == Action.HOLD


def test_only_on_technical_setup_saves_calls(monkeypatch):
    df = frame()
    close = float(df["close"].iloc[-1])
    s = ai(monkeypatch, FakeResponse({"action": "BUY", "confidence": 0.9, "stop_loss": close * 0.98,
                                      "take_profit": close * 1.04, "reason": "ok"}), only_on_technical_setup=True)
    sig = s.on_candle(df)  # this candle has no crossover
    assert sig.action == Action.HOLD and "AI not called" in sig.reason and s._client.calls == []


def test_registry_and_modes():
    s = get_strategy("ai", {"mode": "aggressive"})
    assert s.name == "ai" and s.p.mode == "aggressive"
    with pytest.raises(ValueError):
        get_strategy("ai", {"mode": "reckless"})
