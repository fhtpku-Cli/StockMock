"""旧结构重复打开与分析活性观测。"""

from datetime import timedelta
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.analysis_models import CashStatement, SnapshotQuery
from app.api_models import ConvertInput
from app.engine import Engine
from app.main import create_app
from app.models import Entry, HealthView, Lot, Portfolio, WorkerView
from app.operations import convert
from app.plan_worker import PlanWorker
from app.snapshot_queries import snapshot_range
from app.snapshots import capture
from app.storage import Store
from app.views import portfolio
from tests.test_analysis import evidence
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request, sample


def test_q09_old_round_fields_survive_twice_open(engine: Engine) -> None:
    # Given: 旧结构轮次及缺失新表。
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="600519.SS", quantity=100, cost=Decimal(9000), sellable_at=NOW))
        state.ledger.append(
            Entry(id="old", kind="未知旧类型", amount=Decimal(0), currency="CNY", note="旧说明", created_at=NOW)
        )
        engine.store.save(connection, state)
        _ = connection.execute("DROP TABLE snapshots")
        _ = connection.execute("DROP TABLE adjustments")
    # When: 新版本重复打开真实SQLite，读取和计算分析。
    first = Store(engine.store.path)
    second = Store(engine.store.path)
    account = portfolio(Engine(second, engine.provider, engine.catalog), NOW, WorkerView())
    # Then: 原持久字段不改变，新增响应字段不要求旧响应包含。
    assert first.read() == second.read() == state
    old_json = account.model_dump(exclude={"composition"})
    positions = account.positions[0].model_dump(
        exclude={"market", "cost", "pnl_percent", "market_value_cny", "weight_percent", "price_time"}
    )
    old_json["positions"] = [positions]
    old = Portfolio.model_validate(old_json)
    assert old.positions[0].quantity == 100
    assert old.composition is None
    evidence("q09-old-structure.json", account.model_dump_json())


def test_q08_beijing_midnight_records_distinct_days(engine: Engine) -> None:
    # Given: 北京时间零点前后两个现金账户实际观察。
    before_midnight = NOW.replace(hour=15, minute=59)
    after_midnight = before_midnight + timedelta(minutes=1)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection).model_copy(update={"created_at": before_midnight})
        engine.store.save(connection, state)
    capture(engine, before_midnight)
    capture(engine, after_midnight)
    # When: 自定范围读取。
    result = snapshot_range(
        engine.store,
        state,
        SnapshotQuery(range="custom", start_date=NOW.date(), end_date=(NOW + timedelta(days=1)).date()),
        after_midnight,
    )
    # Then: 两个北京自然日都保存并含结束日。
    assert result.recorded_days == 2
    assert result.change_cny == 0
    assert result.continuous
    evidence("q08-midnight.json", result.model_dump_json())


def test_health_exposes_plan_worker_progress(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 独立计划worker执行一次真实tick（没有活动计划也算心跳）。
    completed = Event()
    original = PlanWorker.tick

    def observed_tick(worker: PlanWorker) -> None:
        original(worker)
        completed.set()

    monkeypatch.setattr(PlanWorker, "tick", observed_tick)
    app = create_app(engine, PASSWORD)
    # When: 从鉴权HTTP健康接口观察。
    with TestClient(app) as client:
        assert completed.wait(timeout=3)
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        result = HealthView.model_validate_json(client.get("/api/health").content)
    # Then: 原健康字段仍有，计划心跳可读。
    assert result.plan_worker.status == "running"
    assert result.plan_worker.last_run is not None
    assert result.status in {"starting", "running"}
    evidence("health.json", result.model_dump_json())


def test_q05_auto_and_manual_fx_reconcile_independently(engine: Engine) -> None:
    # Given: 买入自动换汇，卖出留下美元，再手动换回人民币。
    now = NOW.replace(hour=14)
    engine.provider = ControlledProvider(quote_time=now)
    buy = engine.place(request().model_copy(update={"symbol": "AAPL", "quantity": 10}), now)
    engine.process("AAPL", (sample(engine, buy, "99"),), now + timedelta(minutes=2))
    sell = engine.place(
        request().model_copy(update={"symbol": "AAPL", "quantity": 10, "side": "sell"}), now + timedelta(minutes=2)
    )
    engine.process("AAPL", (sample(engine, sell, "101"),), now + timedelta(minutes=4))
    convert(
        engine, ConvertInput(request_id=str(uuid4()), currency="USD", amount=Decimal(10)), now + timedelta(minutes=4)
    )
    before = engine.store.read()
    # When: 真实HTTP读取三币种核对。
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        response = client.get("/api/cash-statement")
        result = CashStatement.model_validate_json(response.content)
    # Then: 自动/手动两种换汇各归币种，差额为零，读取不改账。
    assert all(row.difference == 0 for row in result.currencies)
    assert result.currencies[0].ledger_balance == Decimal("993136.50")
    assert result.currencies[1].ledger_balance == Decimal("999.49")
    assert engine.store.read() == before
    evidence("q05-fx-statement.json", response.text)
