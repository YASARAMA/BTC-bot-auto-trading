"""Hard safety limits.

These are compiled into the program on purpose. The configuration file, the Settings page
and the optional research assistant can all move a threshold *within* these bounds, but
none of them can move the bounds themselves: that takes a code change, a review and a
commit. Leverage, short selling and live trading have no switch at all.
"""
from __future__ import annotations

from typing import Final

# Per-trade risk, as a percentage of equity.
HARD_MAX_RISK_PER_TRADE_PCT: Final = 1.0
# All open positions together.
HARD_MAX_OPEN_RISK_PCT: Final = 3.0
# Notional of one position, as a percentage of equity. Spot only, so never above 100.
HARD_MAX_POSITION_PCT: Final = 50.0
HARD_MAX_TOTAL_EXPOSURE_PCT: Final = 100.0
HARD_MAX_POSITIONS: Final = 10
HARD_MAX_DAILY_LOSS_PCT: Final = 3.0
HARD_MAX_DRAWDOWN_PCT: Final = 15.0

# Trading modes that exist. "live" is deliberately absent.
SUPPORTED_MODES: Final = ("paper", "backtest")

LEVERAGE_ALLOWED: Final = False
SHORTING_ALLOWED: Final = False
REAL_MONEY_EXECUTION_IMPLEMENTED: Final = False
