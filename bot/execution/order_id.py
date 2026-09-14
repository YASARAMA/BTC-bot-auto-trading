"""Idempotent client order ids.

The id is a pure function of (strategy, symbol, candle timestamp, side). If the bot
restarts and re-evaluates the same candle it produces the same id, and both the local
store and the exchange reject a duplicate, so one candle can never produce two entries.
"""
from __future__ import annotations

import hashlib
import re

PREFIX = "bot"
_HEX_LEN = 24
_VALID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def make_client_order_id(strategy: str, symbol: str, candle_ts: int, side: str, attempt: int = 0) -> str:
    """`attempt` distinguishes a second order for the same candle - a market order placed
    after a maker limit went unfilled. Attempt 0 hashes exactly as it always did, so ids
    written by earlier versions still match."""
    raw = f"{strategy}|{symbol}|{int(candle_ts)}|{side.lower()}"
    if attempt:
        raw = f"{raw}|{int(attempt)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_HEX_LEN]
    cid = f"{PREFIX}{digest}"
    assert _VALID.match(cid)
    return cid


def is_bot_order_id(client_order_id: str | None) -> bool:
    return bool(client_order_id) and client_order_id.startswith(PREFIX) and len(client_order_id) == len(PREFIX) + _HEX_LEN
