"""Position sizing: the risk budget divided by the loss per unit at the stop, then capped.

    risk-based qty = min(risk budget, unused open-risk budget) / loss per unit at the stop

where the loss per unit counts the expected entry slippage and spread, the expected stop
exit slippage and spread, and both fees. The final size is the smallest of the risk-based,
cash-constrained, liquidity-constrained, position-limit and total-exposure sizes, rounded
*down* to the exchange's step size and checked against its minimums. Because a stop can gap,
the realised loss of a stopped trade can exceed the estimate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.execution.models import Costs


@dataclass
class SizingResult:
    qty: float
    notional: float
    risk_amount: float
    per_unit_loss: float
    entry_estimate: float
    binding: str
    candidates: dict[str, float] = field(default_factory=dict)
    rejected: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"qty": self.qty, "notional": round(self.notional, 6), "risk_amount": round(self.risk_amount, 6),
                "per_unit_loss": self.per_unit_loss, "entry_estimate": self.entry_estimate, "binding": self.binding,
                "candidates": {k: (round(v, 10) if math.isfinite(v) else None) for k, v in self.candidates.items()},
                "rejected": self.rejected}


def floor_step(qty: float, step: float | None) -> float:
    if not step or step <= 0:
        return qty
    units = math.floor(qty / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(units * step, decimals + 2)


def size_position(*, equity: float, cash: float, entry_price: float, stop_price: float, risk_pct: float,
                  max_risk_pct: float, open_risk: float, max_open_risk_pct: float, max_position_pct: float,
                  exposure: float, max_total_exposure_pct: float, costs: Costs, avg_bar_volume: float | None,
                  liquidity_max_volume_pct: float, amount_step: float | None, min_qty: float | None,
                  min_notional: float) -> SizingResult:
    entry_est = entry_price * (1 + costs.slippage + costs.half_spread)
    stop_exit = stop_price * (1 - costs.slippage - costs.half_spread)
    per_unit = entry_est * (1 + costs.fee) - stop_exit * (1 - costs.fee)

    def reject(reason: str, candidates: dict[str, float] | None = None) -> SizingResult:
        return SizingResult(0.0, 0.0, 0.0, per_unit, entry_est, "none", candidates or {}, reason)

    if equity <= 0 or entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        return reject("invalid equity, entry or stop")
    if per_unit <= 0:
        return reject("the stop implies no loss per unit; refusing to size")
    budget = equity * min(risk_pct, max_risk_pct) / 100
    unused_open = equity * max_open_risk_pct / 100 - open_risk
    if unused_open <= 0:
        return reject("the aggregate open-risk budget is already used")
    candidates = {
        "risk": min(budget, unused_open) / per_unit,
        "cash": max(0.0, cash) / (entry_est * (1 + costs.fee)),
        "liquidity": (avg_bar_volume * liquidity_max_volume_pct / 100) if avg_bar_volume and avg_bar_volume > 0
        else math.inf,
        "position_limit": equity * max_position_pct / 100 / entry_est,
        "total_exposure": max(0.0, equity * max_total_exposure_pct / 100 - exposure) / entry_est,
    }
    binding = min(candidates, key=lambda k: candidates[k])
    raw = candidates[binding]
    if not math.isfinite(raw) or raw <= 0:
        return reject(f"no size available ({binding} constraint is zero)", candidates)
    qty = floor_step(raw, amount_step)
    if qty <= 0:
        return reject(f"size rounds to zero at the exchange step {amount_step}", candidates)
    if min_qty and qty < min_qty:
        return reject(f"size {qty} is below the exchange minimum quantity {min_qty}", candidates)
    notional = qty * entry_est
    if notional < min_notional:
        return reject(f"order value {notional:.2f} is below the minimum {min_notional:.2f}", candidates)
    return SizingResult(qty, notional, qty * per_unit, per_unit, entry_est, binding, candidates)
