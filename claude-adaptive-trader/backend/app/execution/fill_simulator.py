"""Conservative fill model shared by backtests and the paper broker.

Rules, chosen so that a simulation errs against the strategy rather than for it:

* A decision is taken on the close of candle t and executes at the *open of candle t+1*,
  never at the close it was computed from.
* Market orders pay slippage plus half the spread, plus square-root market impact
  (the candle's ATR as volatility, scaled by the square root of participation).
* A fill may not exceed ``max_participation`` of the candle's volume; the rest of an entry is
  cancelled (a partial fill), the rest of an exit waits for the next candle.
* A stop is a market order once touched: it fills at the stop, or at the open when the
  candle gaps through it, minus slippage. A stop does not guarantee its price.
* A take-profit limit only fills when the price trades *through* it by a buffer; a touch is
  not enough. It fills at its limit price, never better.
* When a candle reaches both the stop and the target, the stop is assumed to have come first.
* No volume or no candle means no fill: the order is rejected or waits, it is never priced
  from an invented value.
"""
from __future__ import annotations

import math

from app.execution.models import Bar, Costs, FillResult


class FillSimulator:
    def __init__(self, costs: Costs) -> None:
        self.costs = costs

    def _impact(self, bar: Bar, qty: float) -> float:
        if not self.costs.market_impact or bar.volume <= 0 or not bar.atr or bar.close <= 0:
            return 0.0
        participation = min(1.0, qty / bar.volume)
        return (bar.atr / bar.close) * math.sqrt(participation)

    def _cap(self, bar: Bar, qty: float) -> float:
        return min(qty, self.costs.max_participation * bar.volume)

    def market(self, side: str, bar: Bar, qty: float, *, ref_price: float | None = None,
               gapped: bool = False) -> FillResult:
        ref = bar.open if ref_price is None else ref_price
        if qty <= 0:
            return FillResult(0, 0, ref, 0, 0, 0, False, gapped, rejected="quantity is zero")
        if bar.volume <= 0:
            return FillResult(0, 0, ref, 0, 0, 0, False, gapped, rejected="no traded volume in the execution candle")
        filled = self._cap(bar, qty)
        impact = self._impact(bar, filled)
        adverse = self.costs.slippage + self.costs.half_spread + impact
        price = ref * (1 + adverse) if side == "buy" else ref * (1 - adverse)
        fee = filled * price * self.costs.fee
        slip = filled * abs(price - ref)
        return FillResult(filled, price, ref, fee, slip, impact * 100, filled + 1e-15 < qty, gapped)

    def stop_triggered(self, bar: Bar, stop: float) -> tuple[float, bool] | None:
        """``(reference price, gapped)`` if the candle hit a sell stop, else ``None``."""
        if bar.open <= stop:
            return bar.open, True
        if bar.low <= stop:
            return stop, False
        return None

    def target_filled(self, bar: Bar, target: float) -> bool:
        return bar.high >= target * (1 + self.costs.take_profit_trade_through)

    def limit_sell(self, bar: Bar, qty: float, limit: float) -> FillResult:
        filled = self._cap(bar, qty)
        fee = filled * limit * self.costs.fee
        return FillResult(filled, limit, limit, fee, 0.0, 0.0, filled + 1e-15 < qty)
