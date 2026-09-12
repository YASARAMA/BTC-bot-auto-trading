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


def make_client_order_id(strategy: str, symbol: str, candle_ts: int, side: str) -> str:
    raw = f"{strategy}|{symbol}|{int(candle_ts)}|{side.lower()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_HEX_LEN]
    cid = f"{PREFIX}{digest}"
    assert _VALID.match(cid)
    return cid


def is_bot_order_id(client_order_id: str | None) -> bool:
    return bool(client_order_id) and client_order_id.startswith(PREFIX) and len(client_order_id) == len(PREFIX) + _HEX_LEN
