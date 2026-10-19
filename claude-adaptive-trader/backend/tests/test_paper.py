"""Paper trading: replay end to end, restart recovery, duplicates, kill switch, live freshness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.core.events import EventBus
from app.data.binance_client import BinancePublicClient
from app.data.historical_store import CandleStore
from app.data.importer import import_csv
from app.data.market_data import MarketDataService
from app.execution.paper_trader import PaperStart, PaperTrader
from app.monitoring.health import HealthMonitor
from app.storage.database import Database
from app.storage.schema import MARKET_MIGRATIONS, STATE_MIGRATIONS
from tests.fakes import FakeExchange, synthetic_candles

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "samples" / "binance_BTCUSDT_1h_2020-11_2021-05.csv"
H1 = 3_600_000


class Env:
    def __init__(self, tmp_path: Path, clock=None) -> None:
        self.settings = Settings()
        self.db = Database(tmp_path / "trader.sqlite", STATE_MIGRATIONS)
        self.mdb = Database(tmp_path / "market.sqlite", MARKET_MIGRATIONS)
        self.store = CandleStore(self.mdb)
        self.clock = clock or (lambda: 1_800_000_000_000)
        self.market = MarketDataService(lambda: self.settings, self.store,
                                        BinancePublicClient(exchange=FakeExchange(), sleep=lambda s: None),
                                        clock=self.clock)
        self.health = HealthMonitor(self.db)

    def trader(self) -> PaperTrader:
        return PaperTrader(lambda: self.settings, self.db, self.market, self.health, EventBus(),
                           clock=self.clock, autostart=False)


def run_to_end(t: PaperTrader, limit: int = 100_000) -> int:
    n = 0
    while n < limit and t.tick():
        n += 1
    return n


def replay_params(**kw) -> PaperStart:
    base = dict(data_mode="replay", strategy_mode="forward_test", strategy="momentum_breakout", pairs=["BTC/USDT"],
                timeframe="1h", replay_speed=10_000)
    base.update(kw)
    return PaperStart(**base)


@pytest.fixture
def env(tmp_path) -> Env:
    e = Env(tmp_path)
    if SAMPLE.exists():
        import_csv(e.store, SAMPLE, filename=SAMPLE.name, exchange="binance", pair="BTC/USDT", timeframe="1h")
    else:  # pragma: no cover - the sample ships with the repository
        e.store.upsert("binance", "BTC/USDT", "1h", synthetic_candles(4000, "1h"), "test")
    return e


def trades_of(db: Database, sid: str) -> list[dict]:
    return db.query("SELECT position_key, entry_ts, exit_ts, entry_price, exit_price, qty, net_pnl, exit_reason"
                    " FROM trades WHERE run_id = ? ORDER BY entry_ts", (sid,))


def test_replay_runs_end_to_end_with_consistent_accounting(env: Env) -> None:
    t = env.trader()
    st = t.start(replay_params())
    assert st["label"].startswith("HISTORICAL REPLAY")
    steps = run_to_end(t)
    assert steps > 1000
    sid = st["session"]["id"]
    trades = env.db.query("SELECT * FROM trades WHERE run_id = ?", (sid,))
    for tr in trades:
        assert tr["net_pnl"] == pytest.approx(tr["gross_pnl"] - tr["fees"])
        assert tr["exit_ts"] > tr["entry_ts"]
    core = t.core
    core.portfolio.check_invariants(core.marks)
    eq = env.db.query("SELECT equity FROM equity_curve WHERE run_id = ? ORDER BY ts", (sid,))
    assert len(eq) == steps
    expected = core.portfolio.equity(core.marks)
    assert eq[-1]["equity"] == pytest.approx(expected)
    entries = env.db.query("SELECT checks FROM decisions WHERE session_id = ? AND action = 'enter'", (sid,))
    import json
    assert all(len(json.loads(e["checks"])) == 14 for e in entries)
    filled = env.db.query("SELECT * FROM orders WHERE session_id = ? AND status = 'filled' AND side = 'buy'", (sid,))
    assert len(filled) >= len(trades)


def test_restart_recovers_exactly_and_never_duplicates(env: Env, tmp_path: Path) -> None:
    # Reference: one uninterrupted replay.
    ref = env.trader()
    ref_sid = ref.start(replay_params())["session"]["id"]
    run_to_end(ref)
    reference = trades_of(env.db, ref_sid)
    assert reference, "the sample must produce at least one trade for this test to mean something"
    ref.stop()

    # Interrupted: process part of it, "crash", restore into a new process-like instance, finish.
    first = env.trader()
    sid = first.start(replay_params())["session"]["id"]
    for _ in range(1700):
        first.tick()
    before = first.status()
    first.session["status"] = "running"            # as if the process died while running
    second = env.trader()
    second.restore()
    after = second.status()
    assert after["session"]["id"] == sid
    assert after["portfolio"]["cash"] == pytest.approx(before["portfolio"]["cash"])
    assert [p["pair"] for p in after["positions"]] == [p["pair"] for p in before["positions"]]
    assert len(after["pending_orders"]) == len(before["pending_orders"])
    second.session["status"] = "running"
    run_to_end(second)
    resumed = trades_of(env.db, sid)
    strip = lambda rows: [{k: v for k, v in r.items() if k != "position_key"} for r in rows]  # noqa: E731
    assert strip(resumed) == strip(reference)          # identical to the uninterrupted run
    keys = [r["position_key"] for r in resumed]
    assert len(keys) == len(set(keys))


def test_reprocessing_a_candle_cannot_duplicate_orders(env: Env) -> None:
    t = env.trader()
    sid = t.start(replay_params())["session"]["id"]
    run_to_end(t)
    orders_before = env.db.scalar("SELECT COUNT(*) FROM orders WHERE session_id = ?", (sid,))
    trades_before = env.db.scalar("SELECT COUNT(*) FROM trades WHERE run_id = ?", (sid,))
    # Wind the cursor back 300 candles and replay them on top of the committed state.
    env.db.execute("UPDATE paper_cursors SET last_processed_ts = last_processed_ts - ? WHERE session_id = ?",
                   (300 * H1, sid))
    t2 = env.trader()
    t2._load(sid)
    t2.session["status"] = "running"
    run_to_end(t2)
    rows = env.db.query("SELECT client_order_id, COUNT(*) AS n FROM orders WHERE session_id = ?"
                        " GROUP BY client_order_id HAVING n > 1", (sid,))
    assert rows == []
    assert env.db.scalar("SELECT COUNT(*) FROM trades WHERE run_id = ?", (sid,)) >= trades_before
    dup_trades = env.db.query("SELECT position_key, COUNT(*) AS n FROM trades WHERE run_id = ? GROUP BY position_key"
                              " HAVING n > 1", (sid,))
    assert dup_trades == []
    assert env.db.scalar("SELECT COUNT(*) FROM orders WHERE session_id = ?", (sid,)) >= orders_before


def test_kill_switch_blocks_entries_and_cancels_pending(env: Env) -> None:
    t = env.trader()
    sid = t.start(replay_params())["session"]["id"]
    t.set_kill_switch(True, actor="test", reason="testing")
    run_to_end(t)
    assert env.db.scalar("SELECT COUNT(*) FROM orders WHERE session_id = ? AND side = 'buy' AND status = 'filled'",
                         (sid,)) == 0
    rejected = env.db.query("SELECT accepted, checks FROM decisions WHERE session_id = ? AND action = 'enter'", (sid,))
    assert rejected and not any(r["accepted"] for r in rejected)
    for r in rejected:
        # Other checks may fail first; the kill switch check fails on every entry regardless.
        health = {c["name"]: c for c in json.loads(r["checks"])}["execution_engine_health"]
        assert health["passed"] is False and "kill switch" in health["detail"]
    assert t.status()["kill_switch"]["on"] is True
    t.set_kill_switch(False, actor="test", reason="done")
    assert t.status_lite()["kill_switch"] is False


def test_live_mode_trades_only_on_the_latest_candle(tmp_path: Path) -> None:
    now = {"t": 0}
    env = Env(tmp_path, clock=lambda: now["t"])
    df = synthetic_candles(3200, "15m", start=1_700_000_000_000, drift=0.0003, vol=0.004, seed=9)
    env.store.upsert("binance", "BTC/USDT", "15m", df.iloc[:3000], "binance-rest")
    now["t"] = int(df["ts"].iloc[2999]) + 900_000 + 5_000
    t = env.trader()
    st = t.start(PaperStart(data_mode="live", strategy_mode="forward_test", strategy="trend_following",
                            pairs=["BTC/USDT"], timeframe="15m"))
    assert st["label"].startswith("PAPER TRADING")
    assert t.tick() is False                       # nothing new since the start
    # Ten new candles arrive while the app is down; it comes back afterwards.
    env.store.upsert("binance", "BTC/USDT", "15m", df.iloc[3000:3010], "binance-ws")
    now["t"] = int(df["ts"].iloc[3009]) + 900_000 + 5_000
    assert t.tick() is True
    fresh = [t._freshness(t._pair_state("BTC/USDT", df.iloc[:i + 1]), i)[0] for i in (3005, 3009)]
    assert fresh == [False, True]                  # old candles are processed but never traded
    sid = st["session"]["id"]
    assert env.db.scalar("SELECT last_processed_ts FROM paper_cursors WHERE session_id = ?", (sid,)) == int(
        df["ts"].iloc[3009])
