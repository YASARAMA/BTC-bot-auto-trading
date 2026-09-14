"""LiveExchange: ccxt-backed spot access. Also used key-less for public market data
in paper mode. Every network call is retried with backoff."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from bot.common import retry_call
from bot.config import ExchangeConfig, Secrets
from bot.execution.base import ExchangeClient, MarketInfo
from bot.models import Order, Side

log = logging.getLogger(__name__)


def _ccxt_retryable() -> tuple[type[BaseException], ...]:
    import ccxt

    return (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout, ccxt.DDoSProtection)


class LiveExchange(ExchangeClient):
    def __init__(
        self,
        cfg: ExchangeConfig,
        secrets: Secrets | None = None,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self._sleep = sleep
        if client is None:
            import ccxt

            if not hasattr(ccxt, cfg.id):
                raise ValueError(f"ccxt has no exchange {cfg.id!r}")
            params: dict[str, Any] = {
                "enableRateLimit": True,
                "options": {"defaultType": "spot", "createMarketBuyOrderRequiresPrice": True},
            }
            if secrets and secrets.has_api_keys:
                params["apiKey"] = secrets.api_key
                params["secret"] = secrets.api_secret
                if secrets.api_password:
                    params["password"] = secrets.api_password
            client = getattr(ccxt, cfg.id)(params)
            if cfg.sandbox:
                client.set_sandbox_mode(True)
        self.client = client
        self._markets_loaded = False
        try:
            self._retryable = _ccxt_retryable()
        except ImportError:  # tests with a fake client and no ccxt
            self._retryable = (ConnectionError, TimeoutError)

    # ----- helpers ---------------------------------------------------------------------
    def _call(self, what: str, fn: Callable[[], Any]) -> Any:
        return retry_call(
            fn,
            retries=self.cfg.max_retries,
            base_seconds=self.cfg.backoff_base_seconds,
            max_seconds=self.cfg.backoff_max_seconds,
            exceptions=self._retryable,
            logger=log,
            what=what,
            sleep=self._sleep,
        )

    def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            self._call("load_markets", self.client.load_markets)
            self._markets_loaded = True

    # ----- MarketData ------------------------------------------------------------------
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None) -> list[list[float]]:
        self._ensure_markets()
        rows = self._call("fetch_ohlcv", lambda: self.client.fetch_ohlcv(symbol, timeframe, since, limit))
        return [[float(x) if x is not None else float("nan") for x in r[:6]] for r in rows]

    def fetch_ticker_price(self, symbol: str) -> float:
        self._ensure_markets()
        t = self._call("fetch_ticker", lambda: self.client.fetch_ticker(symbol))
        price = t.get("last") or t.get("close") or t.get("bid") or t.get("ask")
        if price is None:
            raise RuntimeError(f"ticker for {symbol} has no price: {t}")
        return float(price)

    def market_info(self, symbol: str) -> MarketInfo:
        self._ensure_markets()
        m = self.client.market(symbol)
        precision = m.get("precision") or {}
        limits = m.get("limits") or {}
        amount_step = _precision_to_step(precision.get("amount"), getattr(self.client, "precisionMode", None))
        price_step = _precision_to_step(precision.get("price"), getattr(self.client, "precisionMode", None))
        return MarketInfo(
            amount_step=amount_step,
            price_step=price_step,
            min_amount=float((limits.get("amount") or {}).get("min") or 0.0),
            min_notional=float((limits.get("cost") or {}).get("min") or 0.0),
        )

    # ----- account --------------------------------------------------------------------
    def fetch_balance(self) -> dict[str, dict[str, float]]:
        self._ensure_markets()
        bal = self._call("fetch_balance", self.client.fetch_balance)
        out: dict[str, dict[str, float]] = {}
        for asset, total in (bal.get("total") or {}).items():
            free = (bal.get("free") or {}).get(asset) or 0.0
            out[asset] = {"free": float(free), "total": float(total or 0.0)}
        return out

    def create_market_order(
        self, symbol: str, side: Side, amount: float, client_order_id: str, ref_price: float | None = None
    ) -> Order:
        self._ensure_markets()
        amt = float(self.client.amount_to_precision(symbol, amount))
        params = {"clientOrderId": client_order_id}
        # Price is only used by exchanges that need a quote estimate for market buys (e.g. binance).
        raw = self._call(
            "create_order",
            lambda: self.client.create_order(symbol, "market", side.value, amt, ref_price, params),
        )
        return self.parse_order(raw, client_order_id=client_order_id, fallback_amount=amt, fallback_side=side)

    def create_limit_order(
        self, symbol: str, side: Side, amount: float, price: float, client_order_id: str
    ) -> Order:
        self._ensure_markets()
        amt = float(self.client.amount_to_precision(symbol, amount))
        px = float(self.client.price_to_precision(symbol, price))
        params = {"clientOrderId": client_order_id}
        raw = self._call(
            "create_order",
            lambda: self.client.create_order(symbol, "limit", side.value, amt, px, params),
        )
        parsed = self.parse_order(raw, client_order_id=client_order_id, fallback_amount=amt, fallback_side=side)
        if parsed.price is None:
            parsed.price = px
        return parsed

    def fetch_order(self, order: Order) -> Order:
        self._ensure_markets()
        if order.exchange_order_id:
            raw = self._call("fetch_order", lambda: self.client.fetch_order(order.exchange_order_id, order.symbol))
        else:
            raw = self._call(
                "fetch_order_by_cid",
                lambda: self.client.fetch_order(None, order.symbol, {"clientOrderId": order.client_order_id}),
            )
        parsed = self.parse_order(raw, client_order_id=order.client_order_id, fallback_amount=order.amount, fallback_side=order.side)
        parsed.candle_ts, parsed.kind, parsed.reason, parsed.created_at = order.candle_ts, order.kind, order.reason, order.created_at
        return parsed

    def cancel_order(self, order: Order) -> Order:
        self._ensure_markets()
        try:
            self._call("cancel_order", lambda: self.client.cancel_order(order.exchange_order_id, order.symbol))
        except Exception as exc:  # noqa: BLE001 - order may already be final; refresh below tells us
            log.warning("cancel_order %s failed: %s", order.client_order_id, exc)
        return self.fetch_order(order)

    def fetch_open_orders(self, symbol: str) -> list[Order]:
        self._ensure_markets()
        raw = self._call("fetch_open_orders", lambda: self.client.fetch_open_orders(symbol))
        return [self.parse_order(r) for r in raw]

    # ----- parsing ---------------------------------------------------------------------
    def parse_order(
        self,
        raw: dict[str, Any],
        *,
        client_order_id: str | None = None,
        fallback_amount: float | None = None,
        fallback_side: Side | None = None,
    ) -> Order:
        status_map = {"open": "open", "closed": "closed", "canceled": "canceled", "cancelled": "canceled",
                      "expired": "canceled", "rejected": "rejected"}
        raw_status = (raw.get("status") or "open").lower()
        status = status_map.get(raw_status, "open")
        filled = float(raw.get("filled") or 0.0)
        amount = float(raw.get("amount") or fallback_amount or 0.0)
        # Only a fill has an average price. A resting limit order reports its limit under
        # "price", which must not be mistaken for a price it traded at.
        avg = raw.get("average")
        side = Side(raw["side"]) if raw.get("side") else (fallback_side or Side.BUY)
        symbol = raw.get("symbol") or self.cfg.symbol
        quote = symbol.split("/")[1].split(":")[0] if "/" in symbol else self.cfg.quote
        base = symbol.split("/")[0]
        fee_quote = 0.0
        fees = raw.get("fees") or ([raw["fee"]] if raw.get("fee") else [])
        for f in fees:
            if not f or f.get("cost") is None:
                continue
            cost = float(f["cost"])
            cur = f.get("currency")
            if cur == quote or cur is None:
                fee_quote += cost
            elif cur == base and avg:
                fee_quote += cost * float(avg)
            else:
                # Fee in a third currency (e.g. BNB). Best effort: unknown value, logged not guessed.
                log.info("order fee in %s (%s) not converted to %s", cur, cost, quote)
        ts = int(raw.get("timestamp") or int(time.time() * 1000))
        last = int(raw.get("lastTradeTimestamp") or ts)
        if status == "closed" and amount > 0 and filled == 0.0 and raw_status == "closed":
            filled = amount  # some venues omit filled on closed market orders
        if avg is None and filled > 0:
            avg = raw.get("price")  # ...and some report only "price" on a filled order
        return Order(
            client_order_id=str(raw.get("clientOrderId") or client_order_id or raw.get("id")),
            exchange_order_id=str(raw.get("id")) if raw.get("id") is not None else None,
            symbol=symbol,
            side=side,
            type=str(raw.get("type") or "market"),
            price=float(raw["price"]) if raw.get("price") else None,
            amount=amount,
            filled=filled,
            avg_price=float(avg) if avg else None,
            fee=fee_quote,
            status=status,  # type: ignore[arg-type]
            created_at=ts,
            updated_at=last,
            raw=raw,
        )


def _precision_to_step(value: Any, precision_mode: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # ccxt: TICK_SIZE == 4 -> value is already the step; DECIMAL_PLACES (2) -> number of decimals.
    if precision_mode == 4 or v < 1.0 and not float(v).is_integer():
        return v
    return 10.0 ** (-int(v))
