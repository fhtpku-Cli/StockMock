"""跨 HTTP 与交易场景共享隔离数据库 fixture。"""

import pytest

from tests.test_engine import engine

__all__ = ["engine"]


@pytest.fixture(autouse=True)
def test_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
