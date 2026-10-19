"""The data behind the market chart: candles plus indicator overlays computed here.

Indicators are computed by the same tested functions the strategies use, on a longer
window than is displayed, so that the first visible EMA 200 value is already warmed up.
Values that do not exist yet (warm-up) are sent as ``null``, never as zero.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app.data.market_data import CandleSet
from app.indicators import atr, bollinger, ema, rsi

CHART_WARMUP = 600


def _points(times: list[int], values: pd.Series) -> list[dict[str, Any]]:
    out = []
    for t, v in zip(times, values.tolist()):
        out.append({"time": t, "value": None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, 8)})
    return out


def chart_payload(cs: CandleSet, limit: int) -> dict[str, Any]:
    df = cs.candles.reset_index(drop=True)
    ind = pd.DataFrame({
        "ema20": ema(df["close"], 20),
        "ema50": ema(df["close"], 50),
        "ema200": ema(df["close"], 200),
        "rsi14": rsi(df["close"], 14),
        "atr14": atr(df, 14),
    })
    bb = bollinger(df["close"], 20, 2.0)
    shown = df.tail(limit)
    idx = shown.index
    times = [int(ts // 1000) for ts in shown["ts"]]
    candles = [
        {"time": int(r.ts // 1000), "ts": int(r.ts), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
         "volume": r.volume, "source": getattr(r, "source", None)}
        for r in shown.itertuples(index=False)
    ]
    indicators = {name: _points(times, ind[name].loc[idx]) for name in ind.columns}
    for name in ("bb_upper", "bb_mid", "bb_lower"):
        indicators[name] = _points(times, bb[name].loc[idx])
    forming = None
    if cs.forming:
        f = cs.forming
        forming = {"time": int(f["ts"] // 1000), "ts": int(f["ts"]), "open": f["open"], "high": f["high"],
                   "low": f["low"], "close": f["close"], "volume": f["volume"], "source": f.get("source")}
    return {
        "exchange": cs.exchange, "pair": cs.pair, "timeframe": cs.timeframe,
        "candles": candles, "forming": forming, "indicators": indicators,
        "sources": cs.sources, "derived_from": cs.derived_from, "freshness": cs.freshness, "notes": cs.notes,
        "data_label": data_label(cs),
    }


def data_label(cs: CandleSet) -> str:
    sources = set(cs.sources)
    if cs.derived_from:
        return f"{cs.exchange} {cs.timeframe} built from {cs.derived_from} candles"
    if any(s.startswith("local-file") for s in sources):
        names = ", ".join(sorted(s.split(":", 1)[1] for s in sources if s.startswith("local-file")))
        return f"{cs.exchange} data imported from file ({names})"
    if sources & {"binance-ws", "binance-rest"}:
        return "Binance public market data"
    return f"{cs.exchange} data ({', '.join(sorted(sources))})"
