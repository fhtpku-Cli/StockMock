"""真实回环 HTTP 与独立 SQLite 验证未来日期；时钟和行情受控。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

import pytest
from pydantic import TypeAdapter

from app import market_routes, trading_routes
from app.catalog import CATALOG
from app.engine import Engine
from app.main import create_app
from app.market_session_models import MarketSessionView
from app.models import OrderView, Preview
from tests.market_session_support import Clock, dump, receipt
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import ControlledProvider, request, sample

INTRADAY: Final = datetime(2027, 12, 31, 15, 35, tzinfo=UTC)
AFTER_CLOSE: Final = datetime(2027, 12, 31, 21, 1, tzinfo=UTC)


@pytest.fixture
def us_year_end(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    # Given: 正式目录资格、独立 SQLite、已备美元、真实日历，禁用自动后台。
    engine.catalog = CATALOG
    engine.clock = lambda: Clock.current
    engine.provider = ControlledProvider(quote_time=INTRADAY - timedelta(minutes=1), history_day=date(2027, 12, 30))
    monkeypatch.setattr(Clock, "current", INTRADAY)
    monkeypatch.setattr(trading_routes, "datetime", Clock)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(20000)
        engine.store.save(connection, state)
    return engine


def test_real_http_when_us_year_end_order_fills_once(us_year_end: Engine) -> None:
    # Given: 年末隔离账户和受控报价。
    engine = us_year_end
    body = request().model_copy(update={"symbol": "AAPL"})
    rows: list[str] = []
    app = create_app(engine, PASSWORD, run_worker=False)
    # When: 经真实 HTTP 预览/下单，处理完整区间并重放。
    with server(app) as url, client() as http:
        denied = http.post(url + "/api/orders", json=body.model_dump(mode="json"))
        rows.append(f"未认证下单 {denied.status_code} {denied.text}")
        assert denied.status_code == 401
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        before = dump(engine.store.path)
        preview = http.post(url + "/api/orders/preview", json=body.model_dump(mode="json"))
        rows.append(f"盘中预览 {preview.status_code} {preview.text}")
        assert preview.status_code == 200
        assert Preview.model_validate_json(preview.content).active_at == INTRADAY
        assert dump(engine.store.path) == before
        placed = http.post(url + "/api/orders", json=body.model_dump(mode="json"))
        rows.append(f"盘中下单 {placed.status_code} {placed.text}")
        receipt("implementation-us-http.txt", "\n".join(rows))
        assert placed.status_code == 200
        order = OrderView.model_validate_json(placed.content)
        quote = sample(engine, order)
        engine.process(order.symbol, (quote,), quote.market_time + timedelta(seconds=1))
        filled = engine.store.read()
        # Then: 只成交一次，立即可卖，重放不改变账本。
        assert filled.orders[0].status == "filled"
        assert filled.orders[0].fill_price == Decimal(99)
        assert len(filled.lots) == 1
        assert len(filled.ledger) == 2
        assert filled.lots[0].sellable_at == quote.market_time
        filled_dump = dump(engine.store.path)
        receipt("implementation-us-filled.sql", filled_dump)
        engine.process(order.symbol, (quote,), quote.market_time + timedelta(seconds=2))
        assert dump(engine.store.path) == filled_dump
        displayed = http.get(url + "/api/orders")
        rows.append(f"成交查询 {displayed.status_code} {displayed.text}")
        assert displayed.status_code == 200
        assert displayed.json()[0]["status"] == "filled"
        receipt("implementation-us-http.txt", "\n".join(rows))


def test_real_http_when_us_close_preopens_without_rewriting_history(
    us_year_end: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 真实完整区间已经成交的隔离账户，再推进到年末收盘后。
    engine = us_year_end
    original = engine.place(request().model_copy(update={"symbol": "AAPL"}), INTRADAY)
    quote = sample(engine, original)
    engine.process(original.symbol, (quote,), quote.market_time + timedelta(seconds=1))
    filled = engine.store.read()
    monkeypatch.setattr(Clock, "current", AFTER_CLOSE)
    engine.provider = ControlledProvider(quote_time=AFTER_CLOSE - timedelta(minutes=1), history_day=date(2027, 12, 31))
    rows: list[str] = []
    app = create_app(engine, PASSWORD, run_worker=False)
    # When: 经真实 HTTP 预览并提交下个交易日的新单。
    with server(app) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        pending = request().model_copy(update={"symbol": "AAPL", "quantity": 1})
        for route in ["/api/orders/preview", "/api/orders"]:
            response = http.post(url + route, json=pending.model_dump(mode="json"))
            rows.append(f"收盘后 {route} {response.status_code} {response.text}")
            receipt("implementation-us-preopen-http.txt", "\n".join(rows))
            assert response.status_code == 200
            assert TypeAdapter(datetime).validate_python(response.json()["active_at"]) == datetime(
                2028, 1, 3, 14, 30, tzinfo=UTC
            )
        # Then: 只有一次成交，跨年新单成功，旧单/持仓/流水没有重算。
        after = engine.store.read()
        assert len(after.orders) == 2
        assert after.orders[0] == filled.orders[0]
        assert after.lots == filled.lots
        assert after.ledger == filled.ledger
        assert after.cash == filled.cash
        receipt("implementation-us-after-preopen.sql", dump(engine.store.path))
        receipt("implementation-us-preopen-http.txt", "\n".join(rows))


def test_real_http_when_hk_year_end_session_remains_observation_only(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 正式目录保留港股只观察资格，真实 HTTP 和独立 SQLite。
    engine.catalog = CATALOG
    monkeypatch.setattr(Clock, "current", datetime(2027, 12, 31, 3, 59, tzinfo=UTC))
    monkeypatch.setattr(market_routes, "datetime", Clock)
    monkeypatch.setattr(trading_routes, "datetime", Clock)
    app = create_app(engine, PASSWORD, run_worker=False)
    before = dump(engine.store.path)
    rows: list[str] = []
    # When: 认证后读取半日盘时段，再尝试预览和下单。
    with server(app) as url, client() as http:
        assert http.get(url + "/api/markets/HK/session").status_code == 401
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        for hour, phase in [(3, "supported"), (4, "after_hours")]:
            monkeypatch.setattr(Clock, "current", datetime(2027, 12, 31, hour, 59, tzinfo=UTC))
            response = http.get(url + "/api/markets/HK/session")
            rows.append(f"港股时段 {response.status_code} {response.text}")
            assert response.status_code == 200
            view = MarketSessionView.model_validate_json(response.content)
            assert view.phase == phase
            assert view.next_period is not None
            assert view.next_period.start == datetime(2028, 1, 3, 1, 30, tzinfo=UTC)
        body = request().model_copy(update={"symbol": "0700.HK"})
        for route in ["/api/orders/preview", "/api/orders"]:
            response = http.post(url + route, json=body.model_dump(mode="json"))
            rows.append(f"港股交易 {route} {response.status_code} {response.text}")
            receipt("implementation-hk-http.txt", "\n".join(rows))
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "observation_only"
    # Then: 展示时段可用，交易仍拒绝；整个数据库逐行不变。
    assert dump(engine.store.path) == before
    receipt("implementation-hk-unchanged.sql", before)
