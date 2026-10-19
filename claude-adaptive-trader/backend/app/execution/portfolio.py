"""Virtual portfolio accounting.

Identity that always holds (and is tested): equity = cash + market value of positions =
initial cash + realised PnL + unrealised PnL, where both PnL figures are net of every fee
already paid. Spot only: cash can never go negative, and there is no borrowing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.execution.models import ClosedTrade, Costs, FillResult, Order, Position


class AccountingError(RuntimeError):
    pass


@dataclass
class Portfolio:
    initial_cash: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    slippage_paid: float = 0.0
    trades_closed: int = 0

    @classmethod
    def new(cls, cash: float) -> "Portfolio":
        return cls(initial_cash=cash, cash=cash)

    # ----- valuation ---------------------------------------------------------------------
    def market_value(self, marks: dict[str, float]) -> float:
        return sum(p.remaining * marks.get(p.pair, p.entry_price) for p in self.positions.values())

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + self.market_value(marks)

    def exposure(self, marks: dict[str, float]) -> float:
        return self.market_value(marks)

    def unrealized_pnl(self, marks: dict[str, float]) -> float:
        total = 0.0
        for p in self.positions.values():
            mark = marks.get(p.pair, p.entry_price)
            share = p.remaining / p.qty if p.qty else 0.0
            total += p.remaining * (mark - p.entry_price) - p.entry_fee * share
        return total

    def open_risk(self, costs: Costs) -> float:
        """Estimated loss if every open position were stopped out at its stop, from entry."""
        total = 0.0
        for p in self.positions.values():
            stop_exit = p.stop * (1 - costs.slippage - costs.half_spread)
            per_unit = p.entry_price - stop_exit * (1 - costs.fee)
            total += max(0.0, per_unit * p.remaining)
        return total

    # ----- changes -----------------------------------------------------------------------
    def open_position(self, order: Order, fill: FillResult, *, initial_risk: float, key: str) -> Position:
        if order.pair in self.positions:
            raise AccountingError(f"a position in {order.pair} is already open; adding to it is not allowed")
        cost = fill.filled_qty * fill.price + fill.fee
        if cost > self.cash + 1e-9:
            raise AccountingError(f"fill costs {cost:.2f} but only {self.cash:.2f} cash is available")
        self.cash -= cost
        self.fees_paid += fill.fee
        self.slippage_paid += fill.slippage_cost
        pos = Position(
            pair=order.pair, qty=fill.filled_qty, entry_price=fill.price, entry_ts=order.created_ts,
            entry_fee=fill.fee, entry_slippage=fill.slippage_cost, stop=float(order.stop or 0.0),
            target=order.target, invalidation=order.invalidation, initial_risk=initial_risk,
            strategy=order.strategy, strategy_version=order.strategy_version, regime=order.regime,
            environment=order.environment, entry_reason=order.reason, signal_ts=order.signal_ts,
            time_stop_bars=order.time_stop_bars, key=key, high_water=fill.price, low_water=fill.price,
        )
        self.positions[order.pair] = pos
        return pos

    def reduce_position(self, pair: str, fill: FillResult, ts: int, reason: str) -> ClosedTrade | None:
        """Apply an exit fill. Returns the closed trade when nothing is left."""
        pos = self.positions[pair]
        qty = min(fill.filled_qty, pos.remaining)
        proceeds = qty * fill.price - fill.fee
        self.cash += proceeds
        self.fees_paid += fill.fee
        self.slippage_paid += fill.slippage_cost
        # Book each exit fill as it happens, with its share of the entry fee, so that
        # cash + positions = initial + realised + unrealised holds after partial exits too.
        self.realized_pnl += qty * (fill.price - pos.entry_price) - fill.fee - pos.entry_fee * (qty / pos.qty)
        pos.exit_qty += qty
        pos.exit_value += qty * fill.price
        pos.exit_fees += fill.fee
        pos.exit_slippage += fill.slippage_cost
        if pos.remaining > 1e-12:
            return None
        del self.positions[pair]
        exit_price = pos.exit_value / pos.exit_qty
        gross = pos.exit_value - pos.qty * pos.entry_price
        fees = pos.entry_fee + pos.exit_fees
        net = gross - fees
        self.trades_closed += 1
        cost_basis = pos.qty * pos.entry_price + pos.entry_fee
        return ClosedTrade(
            position_key=pos.key, pair=pair, strategy=pos.strategy, strategy_version=pos.strategy_version,
            regime=pos.regime, environment=pos.environment, entry_ts=pos.entry_ts, exit_ts=ts,
            entry_price=pos.entry_price, exit_price=exit_price, qty=pos.qty, stop_price=pos.stop,
            take_profit=pos.target, gross_pnl=gross, fees=fees, slippage_cost=pos.entry_slippage + pos.exit_slippage,
            net_pnl=net, return_pct=net / cost_basis * 100 if cost_basis else 0.0, initial_risk=pos.initial_risk,
            r_multiple=net / pos.initial_risk if pos.initial_risk > 0 else None, exit_reason=reason,
            entry_reason=pos.entry_reason,
            mae_pct=(pos.low_water / pos.entry_price - 1) * 100, mfe_pct=(pos.high_water / pos.entry_price - 1) * 100,
            bars_held=pos.bars_held,
        )

    def check_invariants(self, marks: dict[str, float], tol: float = 1e-6) -> None:
        if self.cash < -tol:
            raise AccountingError(f"cash went negative: {self.cash}")
        lhs = self.equity(marks)
        rhs = self.initial_cash + self.realized_pnl + self.unrealized_pnl(marks)
        if abs(lhs - rhs) > tol * max(1.0, abs(lhs)):
            raise AccountingError(f"equity identity broken: {lhs} != {rhs}")

    def snapshot(self, marks: dict[str, float], costs: Costs) -> dict[str, Any]:
        eq = self.equity(marks)
        return {
            "cash": self.cash, "equity": eq, "exposure": self.exposure(marks),
            "exposure_pct": self.exposure(marks) / eq * 100 if eq else 0.0,
            "open_risk": self.open_risk(costs), "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(marks), "fees_paid": self.fees_paid,
            "slippage_paid": self.slippage_paid, "positions": len(self.positions),
        }
