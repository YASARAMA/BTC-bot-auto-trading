"""Circuit breakers and the kill switch.

* Daily loss: equity down ``daily_loss_limit_pct`` from the start of the UTC day stops new
  entries until the next UTC day.
* Drawdown: equity down ``max_drawdown_pct`` from its peak stops new entries until a person
  resets it (the reset is logged with a note and starts a new peak).
* Kill switch: manual, immediate, persistent across restarts; blocks every new entry.

Breakers never close positions by themselves: open positions keep their stops.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import RiskConfig
from app.core.clock import utc_day_start


@dataclass
class BreakerState:
    day_start_ts: int
    day_start_equity: float
    peak_equity: float
    daily_tripped: bool = False
    daily_tripped_at: int | None = None
    drawdown_tripped: bool = False
    drawdown_tripped_at: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("events", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BreakerState":
        allowed = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "events"}
        return cls(**allowed)


def new_state(equity: float, ts: int) -> BreakerState:
    return BreakerState(day_start_ts=utc_day_start(ts), day_start_equity=equity, peak_equity=equity)


def daily_loss_pct(state: BreakerState, equity: float) -> float:
    return (1 - equity / state.day_start_equity) * 100 if state.day_start_equity > 0 else 0.0


def drawdown_pct(state: BreakerState, equity: float) -> float:
    return (1 - equity / state.peak_equity) * 100 if state.peak_equity > 0 else 0.0


def update(state: BreakerState, equity: float, ts: int, cfg: RiskConfig) -> list[dict[str, Any]]:
    """Advance the breakers to ``ts`` with the latest equity. Returns the events that happened."""
    events: list[dict[str, Any]] = []
    day = utc_day_start(ts)
    if day > state.day_start_ts:
        if state.daily_tripped:
            events.append({"ts": ts, "event": "daily_reset", "detail": "new UTC day: daily loss breaker cleared"})
        state.day_start_ts = day
        state.day_start_equity = equity
        state.daily_tripped = False
        state.daily_tripped_at = None
    if equity > state.peak_equity and not state.drawdown_tripped:
        state.peak_equity = equity
    loss = daily_loss_pct(state, equity)
    if not state.daily_tripped and loss >= cfg.daily_loss_limit_pct:
        state.daily_tripped, state.daily_tripped_at = True, ts
        events.append({"ts": ts, "event": "daily_tripped",
                       "detail": f"daily loss {loss:.2f}% reached the {cfg.daily_loss_limit_pct}% limit"})
    dd = drawdown_pct(state, equity)
    if not state.drawdown_tripped and dd >= cfg.max_drawdown_pct:
        state.drawdown_tripped, state.drawdown_tripped_at = True, ts
        events.append({"ts": ts, "event": "drawdown_tripped",
                       "detail": f"drawdown {dd:.2f}% reached the {cfg.max_drawdown_pct}% limit"})
    state.events.extend(events)
    return events


def reset_drawdown(state: BreakerState, equity: float, ts: int, actor: str, note: str) -> dict[str, Any]:
    event = {"ts": ts, "event": "drawdown_reset", "detail": f"reset by {actor}: {note}",
             "previous_peak": state.peak_equity, "equity": equity}
    state.drawdown_tripped = False
    state.drawdown_tripped_at = None
    state.peak_equity = equity
    state.events.append(event)
    return event
