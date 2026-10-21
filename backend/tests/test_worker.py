"""真实 lifespan 后台循环在没有浏览器请求时完成持久订单。"""

import secrets
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from threading import Event

import pytest
from fastapi.testclient import TestClient

import app.worker as worker_module
from app.engine import Engine
from app.main import create_app
from app.storage import Store
from app.worker import Worker
from tests.test_engine import NOW, ControlledProvider, request


class MarketClock:
    @staticmethod
    def now(_tz: tzinfo | None = None) -> datetime:
        return NOW + timedelta(minutes=6)


def test_lifespan_fills_persisted_order_without_browser(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 已落盘的挂单及完整的下单后五分钟受控市场区间。
    order = engine.place(request(), NOW)
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=5), price=Decimal(99))
    restarted = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    event = Event()
    original = Worker.tick

    def observed_tick(worker: Worker) -> None:
        original(worker)
        event.set()

    monkeypatch.setattr(worker_module, "datetime", MarketClock)
    monkeypatch.setattr(Worker, "tick", observed_tick)
    # When: 启动真实应用 lifespan/Worker.run，不发送任何 HTTP 请求。
    with TestClient(create_app(restarted, secrets.token_urlsafe(24))):
        assert event.wait(timeout=3)
    # Then: 另建 Store 从磁盘读取，成交和两条费用/买入流水已经持久化。
    persisted = Store(engine.store.path).read()
    assert persisted.orders[0].id == order.id
    assert persisted.orders[0].status == "filled"
    assert len(persisted.ledger) == 2
    assert persisted.lots[0].quantity == 100
