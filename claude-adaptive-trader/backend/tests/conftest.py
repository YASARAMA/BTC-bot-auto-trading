"""Shared fixtures. Every test gets its own databases in a temporary directory."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app
from app.services import Services

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


def make_settings(tmp_path: Path, **sections) -> Settings:
    base = load_settings(REPO_CONFIG).model_dump(mode="python")
    base["storage"] = {
        "database": str(tmp_path / "trader.sqlite"),
        "market_database": str(tmp_path / "market.sqlite"),
        "imports_dir": str(tmp_path / "imports"),
        "reports_dir": str(tmp_path / "reports"),
    }
    for section, values in sections.items():
        base[section].update(values)
    return Settings.model_validate(base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def services(settings: Settings) -> Iterator[Services]:
    svc = Services(settings, start_background=False, console_logs=False)
    yield svc


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    app = create_app(services=services, frontend_dist=None)
    with TestClient(app, headers={"X-CAT-Client": "tests"}) as c:
        yield c
