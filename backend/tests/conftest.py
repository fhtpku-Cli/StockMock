"""跨 HTTP 与交易场景共享隔离数据库 fixture。"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.engine import Engine
from app.main import create_app
from tests.round_support import set_clock
from tests.test_api import PASSWORD
from tests.test_engine import engine

__all__ = ["engine"]


@pytest.fixture(autouse=True)
def test_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")


@pytest.fixture
def api(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    set_clock(monkeypatch)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as session:
        assert session.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield session
