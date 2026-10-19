"""Database schemas.

Two files on purpose. ``market.sqlite`` is a cache of public candles: deleting it loses
nothing that cannot be downloaded again. ``trader.sqlite`` is the record of what this
installation did - paper orders, positions, trades, decisions, experiments and logs - and
is never rebuilt from anything.

Every table that records an amount of money holds *simulated* money. Real-money execution
does not exist in this version, so there is no table for it either.
"""
from __future__ import annotations

from app.storage.database import Migration

STATE_V1 = """
CREATE TABLE kv_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE settings_overrides (
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    PRIMARY KEY (section, key)
);

CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    context TEXT
);
CREATE INDEX idx_logs_ts ON logs (ts);
CREATE INDEX idx_logs_level ON logs (level, ts);

CREATE TABLE strategy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    version TEXT NOT NULL,
    params TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('research', 'validated', 'approved', 'rejected', 'retired')),
    created_at INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    parent_id INTEGER REFERENCES strategy_versions (id),
    hypothesis TEXT,
    notes TEXT,
    validation TEXT,
    validation_experiment_id TEXT,
    approved_at INTEGER,
    approved_by TEXT,
    approval_note TEXT,
    UNIQUE (strategy, params_hash)
);

CREATE TABLE datasets (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    pairs TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    development_end_ts INTEGER NOT NULL,
    validation_end_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    test_fingerprint TEXT NOT NULL,
    sources TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    finished_at INTEGER,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    label TEXT,
    hypothesis TEXT,
    created_by TEXT NOT NULL,
    strategies TEXT NOT NULL,
    pairs TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    segment TEXT NOT NULL,
    dataset_id TEXT REFERENCES datasets (id),
    start_ts INTEGER,
    end_ts INTEGER,
    request TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    data_sources TEXT,
    software_version TEXT NOT NULL,
    seed INTEGER,
    metrics TEXT,
    gates TEXT,
    verdict TEXT,
    summary TEXT,
    error TEXT,
    report_dir TEXT,
    uses_test_set INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT
);
CREATE INDEX idx_experiments_created ON experiments (created_at);

CREATE TABLE test_set_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    dataset_id TEXT NOT NULL REFERENCES datasets (id),
    experiment_id TEXT NOT NULL,
    strategies TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL
);

CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('paper', 'backtest')),
    run_id TEXT NOT NULL,
    scenario TEXT NOT NULL DEFAULT 'base',
    segment TEXT,
    position_key TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    regime TEXT NOT NULL,
    environment TEXT,
    entry_ts INTEGER NOT NULL,
    exit_ts INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    qty REAL NOT NULL,
    stop_price REAL,
    take_profit REAL,
    gross_pnl REAL NOT NULL,
    fees REAL NOT NULL,
    slippage_cost REAL NOT NULL,
    net_pnl REAL NOT NULL,
    return_pct REAL NOT NULL,
    initial_risk REAL,
    r_multiple REAL,
    exit_reason TEXT NOT NULL,
    entry_reason TEXT,
    mae_pct REAL,
    mfe_pct REAL,
    bars_held INTEGER,
    UNIQUE (source, run_id, scenario, position_key)
);
CREATE INDEX idx_trades_run ON trades (source, run_id, scenario);
CREATE INDEX idx_trades_exit ON trades (exit_ts);

CREATE TABLE equity_curve (
    source TEXT NOT NULL CHECK (source IN ('paper', 'backtest')),
    run_id TEXT NOT NULL,
    scenario TEXT NOT NULL DEFAULT 'base',
    ts INTEGER NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    exposure REAL NOT NULL,
    open_risk REAL NOT NULL,
    drawdown_pct REAL NOT NULL,
    benchmark REAL,
    PRIMARY KEY (source, run_id, scenario, ts)
);

CREATE TABLE paper_sessions (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'stopped', 'error')),
    data_mode TEXT NOT NULL CHECK (data_mode IN ('live', 'replay')),
    strategy_mode TEXT NOT NULL CHECK (strategy_mode IN ('adaptive', 'forward_test')),
    forward_strategy TEXT,
    pairs TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    exchange TEXT NOT NULL,
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    peak_equity REAL NOT NULL,
    day_start_equity REAL NOT NULL,
    day_start_ts INTEGER NOT NULL,
    clock_ts INTEGER,
    replay_start_ts INTEGER,
    replay_end_ts INTEGER,
    replay_speed REAL,
    config_snapshot TEXT NOT NULL,
    started_at INTEGER,
    stopped_at INTEGER,
    last_error TEXT,
    note TEXT
);

CREATE TABLE paper_cursors (
    session_id TEXT NOT NULL REFERENCES paper_sessions (id),
    pair TEXT NOT NULL,
    last_processed_ts INTEGER NOT NULL,
    PRIMARY KEY (session_id, pair)
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES paper_sessions (id),
    client_order_id TEXT NOT NULL UNIQUE,
    pair TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    purpose TEXT NOT NULL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'filled', 'partially_filled', 'cancelled', 'rejected')),
    requested_qty REAL NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0,
    ref_price REAL,
    fill_price REAL,
    fee REAL NOT NULL DEFAULT 0,
    slippage_cost REAL NOT NULL DEFAULT 0,
    strategy TEXT,
    strategy_version TEXT,
    regime TEXT,
    signal_ts INTEGER,
    created_ts INTEGER NOT NULL,
    filled_ts INTEGER,
    position_id INTEGER,
    reason TEXT,
    reject_reason TEXT
);
CREATE INDEX idx_orders_session ON orders (session_id, created_ts);

CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES paper_sessions (id),
    pair TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    entry_fee REAL NOT NULL,
    entry_slippage REAL NOT NULL,
    stop_price REAL NOT NULL,
    take_profit REAL,
    initial_risk REAL NOT NULL,
    strategy TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    regime TEXT NOT NULL,
    environment TEXT,
    entry_reason TEXT,
    signal_ts INTEGER NOT NULL,
    time_stop_bars INTEGER,
    bars_held INTEGER NOT NULL DEFAULT 0,
    high_water REAL NOT NULL,
    low_water REAL NOT NULL,
    closed_ts INTEGER,
    exit_reason TEXT
);
CREATE UNIQUE INDEX idx_positions_one_open ON positions (session_id, pair) WHERE status = 'open';

CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    regime TEXT NOT NULL,
    regime_confidence REAL,
    strategy TEXT,
    strategy_version TEXT,
    action TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reason TEXT NOT NULL,
    checks TEXT,
    signal TEXT,
    UNIQUE (session_id, pair, ts, action)
);
CREATE INDEX idx_decisions_session ON decisions (session_id, ts);

CREATE TABLE selector_state (
    session_id TEXT NOT NULL,
    pair TEXT NOT NULL,
    active_strategy TEXT,
    challenger TEXT,
    challenger_streak INTEGER NOT NULL DEFAULT 0,
    updated_ts INTEGER NOT NULL,
    PRIMARY KEY (session_id, pair)
);

CREATE TABLE research_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('claude', 'user', 'system')),
    experiment_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    payload TEXT,
    status TEXT NOT NULL CHECK (status IN ('recorded', 'pending_approval', 'approved', 'rejected')),
    decided_by TEXT,
    decided_at INTEGER,
    decision_note TEXT
)
"""

MARKET_V1 = """
CREATE TABLE kv_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE candles (
    exchange TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL,
    ingested_at INTEGER NOT NULL,
    PRIMARY KEY (exchange, pair, timeframe, ts)
) WITHOUT ROWID;

CREATE TABLE series_meta (
    exchange TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    last_sync_at INTEGER,
    last_sync_status TEXT,
    last_error TEXT,
    last_report TEXT,
    PRIMARY KEY (exchange, pair, timeframe)
);

CREATE TABLE market_rules (
    exchange TEXT NOT NULL,
    pair TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (exchange, pair)
)
"""

STATE_MIGRATIONS = [Migration(1, "initial state schema", STATE_V1)]
MARKET_MIGRATIONS = [Migration(1, "initial market data schema", MARKET_V1)]
