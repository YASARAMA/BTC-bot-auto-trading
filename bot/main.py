"""Unattended trading loop: python -m bot.main [--config config.yaml] [--once] [--replay data.csv]

Wakes every poll interval, checks stops against the ticker, and runs the strategy
once per closed candle. A single failure is logged and reported; the loop continues.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from bot.common import iso, now_ms, timeframe_to_ms
from bot.config import BotConfig, LiveSafetyError, Secrets, load_config, load_secrets, resolve_mode
from bot.data.feed import CandleFeed
from bot.engine import TradingEngine
from bot.execution.base import ExchangeClient, MarketData
from bot.execution.executor import OrderExecutor
from bot.execution.paper import PaperExchange
from bot.execution.replay import CsvMarketData
from bot.logging_utils import log_event, setup_logging
from bot.notify.notifier import Notifier
from bot.risk.manager import RiskManager
from bot.state.store import StateStore
from bot.strategy import get_strategy

log = logging.getLogger("bot.main")


@dataclass
class Runtime:
    cfg: BotConfig
    mode: str
    market: MarketData
    exchange: ExchangeClient
    feed: CandleFeed
    engine: TradingEngine
    store: StateStore
    notifier: Notifier
    replay: CsvMarketData | None = None
    replay_delay_seconds: float = 0.0  # throttle replay so a UI can follow it


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m bot.main", description=__doc__)
    p.add_argument("--config", default=None, help="path to config.yaml (default: $BOT_CONFIG or ./config.yaml)")
    p.add_argument("--once", action="store_true", help="run a single cycle and exit")
    p.add_argument("--max-cycles", type=int, default=0, help="stop after N cycles (0 = run forever)")
    p.add_argument("--replay", metavar="CSV", help="paper-trade against candles from a CSV with a simulated clock")
    p.add_argument("--replay-start", type=int, default=None, help="index of the first candle to treat as 'now'")
    p.add_argument("--log-format", choices=["json", "text"], default=None)
    return p.parse_args(argv)


def build_runtime(cfg: BotConfig, secrets: Secrets, mode: str, replay_csv: str | None = None,
                  replay_start: int | None = None) -> Runtime:
    strategy = get_strategy(cfg.strategy.name, cfg.strategy.params)
    if strategy.warmup > cfg.exchange.candle_history:
        raise ValueError(
            f"strategy {strategy.name!r} needs {strategy.warmup} candles of history but "
            f"exchange.candle_history is {cfg.exchange.candle_history}. The bot would never "
            f"finish warming up. Raise candle_history to at least {strategy.warmup} in Settings, "
            f"or use shorter strategy periods.")
    store = StateStore(cfg.state.db_path)
    notifier = Notifier(cfg.notify, secrets, prefix=f"[{cfg.exchange.symbol} {mode}] ")
    replay: CsvMarketData | None = None

    if replay_csv:
        if mode == "live":
            raise LiveSafetyError("--replay cannot be combined with live mode")
        replay = CsvMarketData.from_csv(
            replay_csv, cfg.exchange.timeframe,
            start_index=replay_start if replay_start is not None else strategy.warmup,
            clock_offset_ms=int(cfg.exchange.candle_close_grace_seconds * 1000) + 1_000,
        )
        market: MarketData = replay
        exchange: ExchangeClient = PaperExchange(
            symbol=cfg.exchange.symbol, fee_rate=cfg.exchange.fee_rate, slippage_bps=cfg.exchange.slippage_bps,
            initial_cash=cfg.paper.initial_cash, initial_base=cfg.paper.initial_base, market_data=replay,
            clock=replay.now_ms,
        )
    else:
        from bot.execution.live import LiveExchange

        if mode == "live":
            live = LiveExchange(cfg.exchange, secrets)
            market, exchange = live, live
        else:
            public = LiveExchange(cfg.exchange, None)  # key-less: public market data only
            market = public
            exchange = PaperExchange(
                symbol=cfg.exchange.symbol, fee_rate=cfg.exchange.fee_rate, slippage_bps=cfg.exchange.slippage_bps,
                initial_cash=cfg.paper.initial_cash, initial_base=cfg.paper.initial_base, market_data=public,
            )

    clock = exchange.now_ms
    feed = CandleFeed(
        market, symbol=cfg.exchange.symbol, timeframe=cfg.exchange.timeframe, window=cfg.exchange.candle_history,
        max_retries=cfg.exchange.max_retries, backoff_base_seconds=cfg.exchange.backoff_base_seconds,
        backoff_max_seconds=cfg.exchange.backoff_max_seconds, sleep=(lambda _s: None) if replay else None,
    )
    risk = RiskManager(cfg.risk, fee_rate=cfg.exchange.fee_rate)
    executor = OrderExecutor(
        exchange, store, symbol=cfg.exchange.symbol, strategy_name=strategy.name,
        order_timeout_seconds=cfg.exchange.order_timeout_seconds, order_poll_seconds=cfg.exchange.order_poll_seconds,
        sleep=(lambda _s: None) if replay else time.sleep, clock=clock,
    )
    engine = TradingEngine(cfg=cfg, strategy=strategy, risk=risk, exchange=exchange, executor=executor,
                           store=store, notifier=notifier, clock=clock, mode=mode)
    return Runtime(cfg=cfg, mode=mode, market=market, exchange=exchange, feed=feed, engine=engine,
                   store=store, notifier=notifier, replay=replay)


def run_cycle(rt: Runtime, cycle: int) -> dict[str, Any]:
    cfg = rt.cfg
    now = rt.exchange.now_ms()
    feed = rt.feed
    tf_ms = feed.tf_ms
    grace = int(cfg.exchange.candle_close_grace_seconds * 1000)
    due = feed.latest_ts is None or (feed.last_closed_ts(now) > feed.latest_ts and now >= feed.last_closed_ts(now) + tf_ms + grace)
    summary: dict[str, Any] = {"cycle": cycle, "mode": rt.mode, "now": iso(now)}
    if due:
        new = feed.refresh(now)
        summary["new_candles"] = int(len(new))
        last_done = rt.engine.last_candle_ts
        pending = new[new["ts"] > last_done] if last_done is not None else new.tail(1)
        if len(pending):
            if len(pending) > 1:
                log_event(log, "candles_missed", level=logging.WARNING, count=int(len(pending)) - 1,
                          detail="evaluating only the newest closed candle; stops checked over the whole range")
            out = rt.engine.process_candle(
                feed.candles(), range_high=float(pending["high"].max()), range_low=float(pending["low"].min()))
            summary.update(out)
            return summary
        if len(new):
            summary["skipped"] = "fetched candles were already processed"
    price = rt.exchange.fetch_ticker_price(cfg.exchange.symbol)
    summary["price"] = price
    tick = rt.engine.process_tick(price, now)
    if tick:
        summary["protective"] = tick
    acct = rt.engine.account(price, now)
    summary.update(equity=round(acct.equity, 2), position=rt.engine.position.to_dict() if rt.engine.position else None)
    return summary


def run_loop(rt: Runtime, *, once: bool = False, max_cycles: int = 0,
             stop_event: threading.Event | None = None) -> int:
    """Run cycles until stop_event is set, a signal arrives, or once/max_cycles is reached."""
    cfg = rt.cfg
    stop = stop_event if stop_event is not None else threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: stop.set())
            except (ValueError, OSError):
                pass
    cycle = 0
    errors = 0
    while not stop.is_set():
        cycle += 1
        try:
            summary = run_cycle(rt, cycle)
            log_event(log, "cycle", **summary)
            errors = 0
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            errors += 1
            log.exception("cycle %d failed: %s", cycle, exc)
            log_event(log, "cycle_error", level=logging.ERROR, cycle=cycle, error=f"{type(exc).__name__}: {exc}",
                      consecutive_errors=errors)
            rt.notifier.error(f"cycle {cycle}: {type(exc).__name__}: {exc}")
        if once or (max_cycles and cycle >= max_cycles):
            break
        if rt.replay is not None:
            if rt.replay.exhausted:
                log_event(log, "replay_finished", cycles=cycle)
                break
            rt.replay.advance()
            if rt.replay_delay_seconds > 0:
                stop.wait(rt.replay_delay_seconds)
            continue
        delay = cfg.exchange.poll_interval_seconds
        if errors:
            delay = min(cfg.exchange.backoff_max_seconds, cfg.exchange.backoff_base_seconds * 2 ** min(errors, 8))
        stop.wait(delay)
    if stop.is_set():
        log_event(log, "shutdown", reason="stop requested", cycles=cycle)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    secrets = load_secrets()
    setup_logging(cfg.logging.level, args.log_format or cfg.logging.format)
    try:
        mode = resolve_mode(cfg, secrets)
    except LiveSafetyError as exc:
        log_event(log, "startup_refused", level=logging.ERROR, error=str(exc))
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    log_event(log, "startup", mode=mode, config=cfg.printable(), replay=args.replay,
              notifications="on" if (secrets.telegram_bot_token or secrets.discord_webhook_url) else "off",
              timeframe_ms=timeframe_to_ms(cfg.exchange.timeframe), started=iso(now_ms()))
    if mode == "live":
        log_event(log, "live_mode", level=logging.WARNING,
                  detail="LIVE trading enabled: real orders will be placed. Kill switch: " + cfg.risk.kill_switch_file)
    try:
        rt = build_runtime(cfg, secrets, mode, args.replay, args.replay_start)
    except LiveSafetyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    try:
        rt.engine.reconcile()
    except Exception as exc:  # noqa: BLE001
        log.exception("reconcile failed")
        rt.notifier.error(f"reconcile failed: {exc}")
        return 1
    rt.notifier.send(f"bot started in {mode} mode ({cfg.strategy.name}, {cfg.exchange.timeframe})")
    try:
        return run_loop(rt, once=args.once, max_cycles=args.max_cycles)
    finally:
        rt.engine.persist()
        rt.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
