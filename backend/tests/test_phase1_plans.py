"""隔离 SQLite 上的计划同步、原载荷恢复和观察时效。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import plan_worker
from app.engine import Engine
from app.models import OrderInput, OrderView, Preview
from app.plan_alerts import alerts
from app.plan_models import PlanInput
from app.plan_worker import PlanWorker
from app.plans import create_plan, round_plans
from app.storage import read_rows
from tests.round_support import BarrierProvider, MarketClock, set_clock
from tests.test_engine import NOW, ControlledProvider, request


def sync_request() -> OrderInput:
    return request().model_copy(
        update={
            "save_as_plan": True,
            "target_price": Decimal(99),
            "stop_price": Decimal(101),
            "plan_review_date": NOW.date(),
        }
    )


def test_sync_order_and_original_receipt(engine: Engine, api: TestClient) -> None:
    original = sync_request()
    body = original.model_dump(mode="json", exclude_none=True)
    preview = api.post("/api/orders/preview", json=body)
    assert preview.status_code == 200
    assert preview.json()["warnings"][2:] == [
        "提交后将同时创建进行中计划，计划复盘日期 2026-10-22",
        "当前报价已达到目标价",
        "当前报价已达到止损参考价",
        "提交后，报价仍满足条件时会站内提醒。",
        "只认 20 分钟内的交易时段报价：收盘后约 20 分钟内仍按收盘前报价判断，之后要等下次开市。",
    ]
    assert round_plans(engine.store, engine.store.read().id) == []
    accepted = api.post("/api/orders", json=body)
    assert accepted.status_code == 200
    order = OrderView.model_validate_json(accepted.content)
    state = engine.store.read()
    (plan,) = round_plans(engine.store, state.id)
    assert plan.round_id == state.id
    assert plan.currency == order.currency
    assert plan.direction == "buy"
    assert plan.status == "active"
    assert plan.created_at == plan.updated_at == order.created_at
    assert plan.id == order.plan_id
    assert order.plan_snapshot is not None
    assert order.plan_version == plan.version == order.plan_snapshot.version == 1
    assert order.plan_snapshot.review_date == original.plan_review_date
    repeat = api.post("/api/orders", json=body, headers={"X-StockMock-Retry": "1"})
    receipt = api.get(f"/api/orders/receipts/{original.request_id}")
    assert repeat.json() == receipt.json() == accepted.json()
    assert round_plans(engine.store, state.id) == [plan]
    with engine.store.transaction() as connection:
        (payload,) = read_rows(connection, "SELECT payload FROM requests")
        assert OrderInput.model_validate_json(payload[0]) == original
    _ = engine.cancel(order.id, NOW)
    assert round_plans(engine.store, state.id)[0].status == "active"


def test_sync_insufficient_funds(engine: Engine, api: TestClient) -> None:
    before = engine.store.read()
    response = api.post("/api/orders", json={**sync_request().model_dump(mode="json"), "quantity": 100000})
    assert response.json()["detail"]["code"] == "insufficient_funds"
    assert engine.store.read() == before
    assert round_plans(engine.store, before.id) == []


@pytest.mark.parametrize(
    "change",
    [
        {"side": "sell"},
        {"plan_id": "existing"},
        {"reason": "   "},
        {"reason": ""},
        {"save_as_plan": False, "plan_review_date": "2026-12-01"},
        {"reason": "隔离测试", "plan_review_date": "2026-10-21"},
    ],
)
def test_sync_rejects_invalid_input(api: TestClient, change: dict[str, object]) -> None:
    body = {**request().model_dump(mode="json"), "save_as_plan": True, **change}
    for path in ("/api/orders/preview", "/api/orders"):
        assert api.post(path, json=body).status_code == 422


def test_sync_conflict_blocks_original_and_persists_retry(engine: Engine, api: TestClient) -> None:
    blocked = BarrierProvider()
    engine.provider = blocked
    original = sync_request()
    body = original.model_dump(mode="json")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(api.post, "/api/orders", json=body)
        try:
            assert blocked.entered.wait(timeout=5)
            plan = create_plan(engine, PlanInput(symbol=original.symbol, direction="buy", status="active"), NOW)
            early = api.post("/api/orders/preview", json=body)
            assert early.json()["detail"]["code"] == "plan_sync_conflict"
            ordinary = api.post("/api/orders", json=body)
            assert ordinary.json()["detail"] == early.json()["detail"]
            retry = api.post("/api/orders", json=body, headers={"X-StockMock-Retry": "1"})
        finally:
            blocked.release.set()
        assert pending.result(timeout=5).json() == retry.json()
    assert retry.json()["detail"]["code"] == "order_receipt_rejected"
    assert "该证券刚有进行中计划。可带入该计划，或取消同步后重新预览。" in retry.json()["detail"]["message"]
    assert early.json()["detail"]["message"] == "该证券刚有进行中计划。可带入该计划，或取消同步后重新预览。"
    assert api.get(f"/api/orders/receipts/{original.request_id}").json() == retry.json()
    assert engine.store.read().orders == []
    assert round_plans(engine.store, plan.round_id) == [plan]


def test_sync_preview_without_date_or_reached_condition(api: TestClient) -> None:
    body = {**request().model_dump(mode="json", exclude_none=True), "save_as_plan": True, "target_price": "120"}
    warnings = Preview.model_validate_json(api.post("/api/orders/preview", json=body).content).warnings
    assert warnings[1:] == ["提交后将同时创建进行中计划"]
    assert not any("复盘日期" in warning or "站内提醒" in warning for warning in warnings)


def test_sync_review_date_messages(api: TestClient) -> None:
    body = {**request().model_dump(mode="json"), "save_as_plan": True, "reason": "隔离测试"}
    past = api.post("/api/orders/preview", json={**body, "plan_review_date": "2026-10-21"})
    assert past.json()["detail"] == {
        "code": "invalid_review_date",
        "message": "计划复盘日期不能早于北京时间今天",
    }
    unpaired = api.post("/api/orders", json={**body, "save_as_plan": False, "plan_review_date": "2026-12-01"})
    assert "计划复盘日期仅用于同步创建计划" in unpaired.text
    assert "复查日期" not in past.text + unpaired.text


@pytest.mark.parametrize(("minutes", "expected"), [(2, ["target"]), (21, [])])
def test_after_close_observation(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, minutes: int, expected: list[str]
) -> None:
    closing = datetime(2026, 10, 22, 20, tzinfo=UTC)
    now = closing + timedelta(minutes=minutes)
    engine.provider = ControlledProvider(quote_time=closing, price=Decimal(101))
    engine.plan_provider = engine.provider
    body = request().model_copy(update={"symbol": "AAPL", "save_as_plan": True, "target_price": Decimal(100)})
    order = engine.place(body, closing + timedelta(minutes=1))
    set_clock(monkeypatch, now)
    monkeypatch.setattr(plan_worker, "datetime", MarketClock)
    PlanWorker(engine).tick()
    assert order.plan_id is not None
    assert [event.condition for event in alerts(engine.store, engine.store.read().id)] == expected


def test_sync_retries_across_rounds_leave_archived_plan_alone(engine: Engine, api: TestClient) -> None:
    old_round = engine.store.read().id
    original = sync_request().model_copy(update={"expected_round_id": old_round})
    payload = original.model_dump(mode="json", exclude_none=True)
    order = OrderView.model_validate_json(api.post("/api/orders", json=payload).content)
    _ = engine.cancel(order.id, NOW)
    (plan,) = round_plans(engine.store, old_round)
    reset = api.post("/api/rounds/reset", json={"request_id": str(uuid4()), "confirmation": "重新开始"})
    assert reset.status_code == 200, reset.text
    retry = api.post("/api/orders", json=payload, headers={"X-StockMock-Retry": "1"})
    assert OrderView.model_validate_json(retry.content).id == order.id
    late = api.post("/api/orders", json={**payload, "request_id": str(uuid4())})
    assert late.json()["detail"]["code"] == "round_changed"
    assert round_plans(engine.store, old_round) == [plan]
    assert round_plans(engine.store, engine.store.read().id) == []


def test_legacy_payload_retry_with_new_defaults(engine: Engine, api: TestClient) -> None:
    original = request()
    payload = original.model_dump(mode="json", exclude_none=True)
    accepted = api.post("/api/orders", json=payload)
    assert accepted.status_code == 200
    retry = api.post(
        "/api/orders",
        json={**payload, "save_as_plan": False, "plan_review_date": None},
        headers={"X-StockMock-Retry": "1"},
    )
    assert retry.json() == accepted.json()
    assert len(engine.store.read().orders) == 1
    assert round_plans(engine.store, engine.store.read().id) == []
