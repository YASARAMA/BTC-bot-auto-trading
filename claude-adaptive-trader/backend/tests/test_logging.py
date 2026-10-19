from __future__ import annotations

import logging

from app.monitoring.logging import DatabaseLogHandler, redact, record_to_row
from app.storage.database import Database
from app.storage.schema import STATE_MIGRATIONS


def test_redacts_common_secret_shapes() -> None:
    assert "sk-ant-***" in redact("key sk-ant-abcdefghijklmnop1234")
    assert "abcdef123456" not in redact("api_key=abcdef123456")
    assert "s3cr3tvalue" not in redact('{"secret": "s3cr3tvalue"}')
    assert "Bearer ***" in redact("Authorization: Bearer abcdefghijklmnopqrstu")
    assert redact("max_output_tokens: 4000") == "max_output_tokens: 4000"


def test_context_keys_that_look_secret_are_masked() -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", None, None)
    record.context = {"api_key": "plain", "nested": {"token": "x"}, "pair": "BTC/USDT"}
    row = record_to_row(record)
    assert row["context"]["api_key"] == "***"
    assert row["context"]["nested"]["token"] == "***"
    assert row["context"]["pair"] == "BTC/USDT"


def test_database_handler_persists(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite", STATE_MIGRATIONS)
    handler = DatabaseLogHandler(db)
    logger = logging.getLogger("app.test_db_handler")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.warning("disk almost full", extra={"context": {"free_mb": 12}})
        handler.flush()
    finally:
        logger.removeHandler(handler)
        handler.close()
    rows = db.query("SELECT level, source, message, context FROM logs")
    assert rows and rows[0]["level"] == "WARNING"
    assert rows[0]["source"] == "test_db_handler"
    assert "free_mb" in rows[0]["context"]
