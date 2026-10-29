"""0.4.6 真实回环HTTP与SQLite历史只读性。"""

from decimal import Decimal
from typing import override
from uuid import uuid4

import pytest

from app import asset_routes
from app.engine import Engine
from app.main import create_app
from app.marketdata import Quote
from app.models import Entry, Portfolio, WorkerView
from app.storage import Store
from app.views import portfolio
from tests.portfolio_046_support import QNOW, Clock, mixed
from tests.socket_support import client, server
from tests.test_analysis import evidence
from tests.test_api import PASSWORD
from tests.test_engine import ControlledProvider


def database_dump(store: Store) -> str:
    """比较全部业务表、schema和原始JSON文本，不只比较反序列化结果。"""
    with store.transaction() as connection:
        return "\n".join(connection.iterdump())


def test_actual_http_and_store_reopen_preserve_old_json_and_exports(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 旧JSON省略历史可选字段，保留真实卖单、混合批次及精确金额。
    _ = mixed(engine)
    monkeypatch.setattr(Clock, "current", QNOW)
    monkeypatch.setattr(asset_routes, "datetime", Clock)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.ledger.append(
            Entry(
                id="legacy-cash", kind="legacy", currency="CNY", amount=Decimal("12.34"), created_at=QNOW, note="旧记录"
            )
        )
        state.cash["CNY"] += Decimal("12.34")
        raw = state.model_dump_json(exclude={"orders": {"__all__": {"fill_observation", "cost_basis", "realized_pnl"}}})
        _ = connection.execute("UPDATE rounds SET data=? WHERE id=?", (raw, state.id))
    before = database_dump(engine.store)
    evidence("q04-before.sql", before)
    evidence("q04-before-state.json", raw)
    # When: 真实HTTP鉴权读取，重开SQLite后再次读取。
    with server(create_app(engine, PASSWORD, run_worker=False), port=8146) as url, client() as http:
        denied = http.get(url + "/api/portfolio")
        assert denied.status_code == 401
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        old_orders = http.get(url + "/api/orders")
        old_export = http.get(url + "/api/export.csv")
        old_ledger = http.get(url + "/api/ledger/export.csv")
        assert old_orders.status_code == old_export.status_code == old_ledger.status_code == 200
        response = http.get(url + "/api/portfolio")
        assert response.status_code == 200
        account = Portfolio.model_validate_json(response.content)
        assert account.active_orders is not None
        assert account.active_orders.accepted_count == 1
        assert account.positions[0].sellability is not None
        assert account.positions[0].sellability.locked_quantity == 200
        assert account.cash[0].balance == Decimal("1000012.34")
        assert account.positions[0].average_cost == 90
        assert database_dump(engine.store) == before
        engine.store = Store(engine.store.path)
        reread = http.get(url + "/api/portfolio")
        assert reread.status_code == 200
        assert reread.content == response.content
        assert http.get(url + "/api/orders").content == old_orders.content
        assert http.get(url + "/api/export.csv").content == old_export.content
        assert http.get(url + "/api/ledger/export.csv").content == old_ledger.content
        evidence("q04-portfolio.json", response.text)
        evidence("q04-after-reopen-portfolio.json", reread.text)
        evidence("q04-orders.json", old_orders.text)
        evidence("q04-export.csv", old_export.text)
        evidence("q04-ledger.csv", old_ledger.text)
        evidence(
            "q04-http.txt",
            "\n".join(
                (
                    f"URL {url}",
                    f"unauthenticated={denied.status_code}",
                    f"portfolio={response.status_code}",
                    f"reopen={reread.status_code}",
                )
            ),
        )
    # Then: 十张业务表和State/Order/Lot原始文本完全相同，新增投影未入库。
    after = database_dump(engine.store)
    assert after == before
    assert "active_orders" not in after
    assert "sellability" not in after
    assert before.count("CREATE TABLE ") == 10
    evidence("q04-after.sql", after)
    evidence(
        "q04-read-only-result.txt",
        "10业务表、schema、State/Order/Lot原JSON逐字相同；GET及重开无持久投影；订单和两种CSV字节相同",
    )


def test_old_response_missing_or_null_objects_stays_unknown(engine: Engine) -> None:
    # Given: 新服务真实投影作为完整响应。
    _ = mixed(engine)
    current = portfolio(engine, QNOW, WorkerView())
    old = current.model_dump(exclude={"active_orders": True, "positions": {"__all__": {"sellability"}}})
    # When: 接收旧响应缺对象，以及显式null对象。
    missing = Portfolio.model_validate(old)
    null = Portfolio.model_validate(missing.model_dump())
    # Then: 兼容未知不变成假零，原可卖/占用仍保留。
    assert missing.active_orders is None
    assert missing.positions[0].sellability is None
    assert null.active_orders is None
    assert null.positions[0].sellability is None
    assert null.positions[0].sellable == 200
    assert null.positions[0].reserved == 100
    evidence("q04-old-response.json", missing.model_dump_json())


def test_actual_http_continuous_changes_return_409(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 报价期间持续出现数据库委托更新。
    _ = mixed(engine)
    monkeypatch.setattr(Clock, "current", QNOW)
    monkeypatch.setattr(asset_routes, "datetime", Clock)

    class ChangingProvider(ControlledProvider):
        @override
        def quote(self, symbol: str) -> Quote:
            with engine.store.transaction() as connection:
                state = engine.store.load(connection)
                state.orders.append(state.orders[0].model_copy(update={"id": str(uuid4()), "side": "buy"}))
                engine.store.save(connection, state)
            return super().quote(symbol)

    engine.provider = ChangingProvider(quote_time=QNOW)
    # When: 真正HTTP读取无法取得一致账户。
    with server(create_app(engine, PASSWORD, run_worker=False), port=8146) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        response = http.get(url + "/api/portfolio")
    # Then: API拒绝混合读取，没有附带不一致的正常资产对象。
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "valuation_changed"
    assert "active_orders" not in response.json()
    evidence("q02-http-409.json", response.text)
