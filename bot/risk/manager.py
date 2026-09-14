"""RiskManager: turns a Signal into an OrderIntent or a Rejection.

Entry gates (BUY): kill switch, daily-loss halt, consecutive-loss cooldown, minimum
time between trades, minimum confidence, valid stop. Position size is the smaller of
fixed-fractional risk (risk_per_trade_pct of equity / stop distance), max position
notional (max_position_pct of equity) and affordable cash after fees.

Exits (SELL, stop loss, take profit) are only blocked by the kill switch: a halted or
cooling-down bot must still be able to leave a position.

All thresholds come from RiskConfig. The manager keeps a small RiskState that the
engine persists so halts and cooldowns survive restarts.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

from bot.common import next_utc_midnight, utc_day
from bot.config import RiskConfig
from bot.models import Action, OrderIntent, Position, Rejection, Side, Signal, Trade


@dataclass
class RiskState:
    day: str = ""
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: int = 0
    halted_until: int = 0
    halt_reason: str = ""
    last_entry_ts: int = 0
    trades_today: int = 0
    realized_pnl_today: float = 0.0
    events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("events", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "RiskState":
        if not d:
            return cls()
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "events"}
        return cls(**known)


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    cash: float
    price: float
    now_ms: int
    position: Position | None = None


class RiskManager:
    def __init__(self, cfg: RiskConfig, fee_rate: float, state: RiskState | None = None,
                 slippage_bps: float = 0.0) -> None:
        self.cfg = cfg
        self.fee_rate = fee_rate
        # A market buy fills a little above the price it was sized at. Ignoring that made a
        # position sized to the whole account unaffordable by exactly the slippage, and the
        # exchange rejected it - invisible at the default 25% cap, fatal at 100%.
        self.slippage = slippage_bps / 10_000.0
        self.state = state or RiskState()

    # ----- bookkeeping ---------------------------------------------------------------
    def roll_day(self, now_ms: int, equity: float) -> bool:
        """Reset the daily window at UTC midnight. Returns True when a new day started."""
        day = utc_day(now_ms)
        if day != self.state.day:
            self.state.day = day
            self.state.day_start_equity = equity
            self.state.trades_today = 0
            self.state.realized_pnl_today = 0.0
            if self.state.halted_until and now_ms >= self.state.halted_until:
                self.state.halted_until = 0
                self.state.halt_reason = ""
            return True
        return False

    def kill_switch_active(self) -> bool:
        return bool(self.cfg.kill_switch_file) and os.path.exists(self.cfg.kill_switch_file)

    def daily_drawdown_pct(self, equity: float) -> float:
        start = self.state.day_start_equity
        if start <= 0:
            return 0.0
        return (equity - start) / start * 100.0

    def is_halted(self, now_ms: int) -> bool:
        return now_ms < self.state.halted_until

    def in_cooldown(self, now_ms: int) -> bool:
        return now_ms < self.state.cooldown_until

    def on_trade_closed(self, trade: Trade, equity: float, now_ms: int) -> list[str]:
        """Update loss streak / daily pnl after a closed trade. Returns event strings."""
        events: list[str] = []
        self.roll_day(now_ms, equity)
        self.state.realized_pnl_today += trade.pnl
        if trade.pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        if (
            self.state.consecutive_losses >= self.cfg.max_consecutive_losses
            and self.cfg.cooldown_minutes > 0
        ):
            self.state.cooldown_until = now_ms + int(self.cfg.cooldown_minutes * 60_000)
            events.append(
                f"cooldown: {self.state.consecutive_losses} consecutive losses, "
                f"no entries for {self.cfg.cooldown_minutes:g} minutes"
            )
        halt = self._check_daily_loss(equity, now_ms)
        if halt:
            events.append(halt)
        self.state.events.extend(events)
        return events

    def _check_daily_loss(self, equity: float, now_ms: int) -> str | None:
        dd = self.daily_drawdown_pct(equity)
        if dd <= -self.cfg.max_daily_loss_pct and not self.is_halted(now_ms):
            self.state.halted_until = next_utc_midnight(now_ms)
            self.state.halt_reason = f"daily loss {dd:.2f}% breached limit {self.cfg.max_daily_loss_pct:g}%"
            return f"halt: {self.state.halt_reason}; entries paused until next UTC day"
        return None

    # ----- decisions -----------------------------------------------------------------
    def evaluate(self, signal: Signal, account: AccountSnapshot, candle_ts: int) -> OrderIntent | Rejection:
        st = self.state
        now = account.now_ms
        self.roll_day(now, account.equity)

        if signal.action == Action.HOLD:
            return Rejection(signal.reason or "hold", code="hold")

        if self.kill_switch_active():
            return Rejection(f"kill switch file present: {self.cfg.kill_switch_file}", code="kill_switch")

        if signal.action == Action.SELL:
            if account.position is None or account.position.qty <= 0:
                return Rejection("SELL signal but no open position", code="no_position")
            return OrderIntent(
                side=Side.SELL,
                qty=account.position.qty,
                ref_price=account.price,
                kind="exit",
                candle_ts=candle_ts,
                reason=signal.reason,
                confidence=signal.confidence,
            )

        # ----- BUY: entry gates -----
        if account.position is not None and account.position.qty > 0:
            return Rejection("BUY signal but already long", code="already_long")
        halt = self._check_daily_loss(account.equity, now)
        if halt:
            st.events.append(halt)
        if self.is_halted(now):
            return Rejection(f"halted: {st.halt_reason}", code="halted")
        if self.in_cooldown(now):
            return Rejection(
                f"cooldown after {st.consecutive_losses} consecutive losses "
                f"({(st.cooldown_until - now) / 60_000:.0f} min left)",
                code="cooldown",
            )
        if st.last_entry_ts and now - st.last_entry_ts < self.cfg.min_seconds_between_trades * 1000:
            return Rejection(
                f"min time between trades not elapsed ({(now - st.last_entry_ts) / 1000:.0f}s "
                f"< {self.cfg.min_seconds_between_trades:g}s)",
                code="min_interval",
            )
        if signal.confidence < self.cfg.min_confidence:
            return Rejection(
                f"confidence {signal.confidence:.2f} below minimum {self.cfg.min_confidence:.2f}",
                code="low_confidence",
            )
        price = account.price
        if price <= 0:
            return Rejection("no valid price", code="no_price")
        stopless = signal.stop_loss is None
        if stopless and not self.cfg.allow_entry_without_stop:
            return Rejection("entry needs a stop loss (set risk.allow_entry_without_stop to trade without one)",
                             code="bad_stop")
        if not stopless and (signal.stop_loss <= 0 or signal.stop_loss >= price):
            return Rejection(f"entry needs a stop loss below price (got {signal.stop_loss})", code="bad_stop")

        # ----- sizing -----
        # Fixed-fractional: risk a fixed share of equity between entry and stop. Without a
        # stop there is no distance to divide by, so such an entry is sized by the position
        # cap alone - which is why it has to be asked for explicitly.
        risk_amount = account.equity * self.cfg.risk_per_trade_pct / 100.0
        qty_by_risk = float("inf") if stopless else risk_amount / (price - signal.stop_loss)
        max_notional = account.equity * self.cfg.max_position_pct / 100.0
        qty_by_cap = max_notional / price
        affordable = account.cash / (price * (1.0 + self.fee_rate) * (1.0 + self.slippage))
        qty = max(0.0, min(qty_by_risk, qty_by_cap, affordable))
        notional = qty * price
        if notional < self.cfg.min_order_notional:
            return Rejection(
                f"order notional {notional:.2f} below minimum {self.cfg.min_order_notional:g} "
                f"(risk qty {qty_by_risk:.6f}, cap qty {qty_by_cap:.6f}, affordable {affordable:.6f})",
                code="too_small",
            )
        return OrderIntent(
            side=Side.BUY,
            qty=qty,
            ref_price=price,
            kind="entry",
            candle_ts=candle_ts,
            reason=signal.reason,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
        )

    def protective_exit(
        self, position: Position | None, high: float, low: float, candle_ts: int
    ) -> OrderIntent | Rejection | None:
        """Stop loss / take profit check against a price range. None when nothing triggers.

        If both levels are inside the range the stop wins (conservative assumption).
        """
        if position is None or position.qty <= 0:
            return None
        hit: tuple[str, float] | None = None
        if position.stop_loss is not None and low <= position.stop_loss:
            hit = ("stop_loss", position.stop_loss)
        elif position.take_profit is not None and high >= position.take_profit:
            hit = ("take_profit", position.take_profit)
        if hit is None:
            return None
        if self.kill_switch_active():
            return Rejection(f"{hit[0]} hit but kill switch file present", code="kill_switch")
        kind, level = hit
        return OrderIntent(
            side=Side.SELL,
            qty=position.qty,
            ref_price=level,
            kind=kind,  # type: ignore[arg-type]
            candle_ts=candle_ts,
            reason=f"{kind} {level:.2f} hit (range {low:.2f}-{high:.2f})",
            confidence=1.0,
        )

    def record_entry(self, now_ms: int) -> None:
        self.state.last_entry_ts = now_ms
        self.state.trades_today += 1

    def status(self, now_ms: int, equity: float) -> dict[str, Any]:
        return {
            "kill_switch": self.kill_switch_active(),
            "halted": self.is_halted(now_ms),
            "halt_reason": self.state.halt_reason or None,
            "cooldown": self.in_cooldown(now_ms),
            "consecutive_losses": self.state.consecutive_losses,
            "daily_drawdown_pct": round(self.daily_drawdown_pct(equity), 4),
            "trades_today": self.state.trades_today,
        }
