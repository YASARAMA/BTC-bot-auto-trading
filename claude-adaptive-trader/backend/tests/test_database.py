from __future__ import annotations

import threading

import pytest

from app.storage.database import Database, Migration
from app.storage.schema import MARKET_MIGRATIONS, STATE_MIGRATIONS


def test_migrations_apply_once(tmp_path) -> None:
    path = tmp_path / "state.sqlite"
    db = Database(path, STATE_MIGRATIONS)
    assert db.schema_version == 1
    db.close()
    again = Database(path, STATE_MIGRATIONS)
    assert again.schema_version == 1
    assert again.scalar("SELECT COUNT(*) FROM schema_version") == 1
    assert again.integrity_check() == "ok"


def test_market_schema(tmp_path) -> None:
    db = Database(tmp_path / "m.sqlite", MARKET_MIGRATIONS)
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"candles", "series_meta", "market_rules", "kv_state"} <= tables


def test_later_migration_is_applied(tmp_path) -> None:
    path = tmp_path / "x.sqlite"
    Database(path, [Migration(1, "a", "CREATE TABLE a (x INTEGER)")]).close()
    db = Database(path, [Migration(1, "a", "CREATE TABLE a (x INTEGER)"),
                         Migration(2, "b", "CREATE TABLE b (y INTEGER)")])
    assert db.schema_version == 2


def test_nested_transaction_rolls_back_inner_only(tmp_path) -> None:
    db = Database(tmp_path / "t.sqlite", [Migration(1, "t", "CREATE TABLE t (v INTEGER)")])
    with db.transaction():
        db.execute("INSERT INTO t VALUES (1)")
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.execute("INSERT INTO t VALUES (2)")
                raise RuntimeError("inner failure")
        db.execute("INSERT INTO t VALUES (3)")
    assert [r["v"] for r in db.query("SELECT v FROM t ORDER BY v")] == [1, 3]


def test_outer_failure_rolls_back_everything(tmp_path) -> None:
    db = Database(tmp_path / "t.sqlite", [Migration(1, "t", "CREATE TABLE t (v INTEGER)")])
    with pytest.raises(ValueError):
        with db.transaction():
            db.execute("INSERT INTO t VALUES (1)")
            raise ValueError("boom")
    assert db.scalar("SELECT COUNT(*) FROM t") == 0
    assert not db.in_transaction


def test_concurrent_writers_lose_nothing(tmp_path) -> None:
    db = Database(tmp_path / "t.sqlite", [Migration(1, "t", "CREATE TABLE t (thread INTEGER, n INTEGER)")])
    errors: list[BaseException] = []

    def work(k: int) -> None:
        try:
            for n in range(200):
                with db.transaction():
                    db.execute("INSERT INTO t VALUES (?, ?)", (k, n))
                db.query("SELECT COUNT(*) FROM t")
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert db.scalar("SELECT COUNT(*) FROM t") == 1200


def test_state_round_trip(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite", STATE_MIGRATIONS)
    assert db.get_state("missing", "default") == "default"
    db.set_state("kill_switch", {"on": True, "by": "user"})
    assert db.get_state("kill_switch") == {"on": True, "by": "user"}
    db.delete_state("kill_switch")
    assert db.get_state("kill_switch") is None


def test_one_open_position_per_pair_enforced_by_schema(tmp_path) -> None:
    import sqlite3

    db = Database(tmp_path / "s.sqlite", STATE_MIGRATIONS)
    db.execute(
        "INSERT INTO paper_sessions (id, created_at, updated_at, status, data_mode, strategy_mode, pairs, timeframe,"
        " exchange, initial_cash, cash, peak_equity, day_start_equity, day_start_ts, config_snapshot)"
        " VALUES ('s', 0, 0, 'running', 'replay', 'adaptive', '[]', '15m', 'binance', 1, 1, 1, 1, 0, '{}')")
    row = ("s", "BTC/USDT", "open", 1.0, 100.0, 0, 0.1, 0.0, 90.0, 10.0, "A", "A-1", "trend_up", 0, 100.0, 100.0)
    sql = ("INSERT INTO positions (session_id, pair, status, qty, entry_price, entry_ts, entry_fee, entry_slippage,"
           " stop_price, initial_risk, strategy, strategy_version, regime, signal_ts, high_water, low_water)"
           " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    db.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, row)
