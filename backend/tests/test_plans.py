"""计划事务、旧库升级与委托快照均使用真实临时 SQLite。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.api_models import ResetInput
from app.engine import Engine
from app.marketdata import FxRate, Quote
from app.models import DomainError, Journal, OrderInput
from app.operations import reset
from app.plan_models import Plan, PlanInput, PlanUpdate
from app.plan_reviews import reviews
from app.plans import create_plan, get_plan, round_plans, update_plan
from app.storage import Store
from tests.test_engine import NOW, ControlledProvider, request, sample


def body() -> PlanInput:
    return PlanInput(
        symbol="600519.SS",
        direction="buy",
        status="active",
        reason="先记录判断",
        target_price=Decimal(110),
        stop_price=Decimal(90),
        review_date=NOW.date(),
        invalidation="逻辑失效",
    )


def edit(plan: Plan, reason: str = "修订依据") -> PlanUpdate:
    return PlanUpdate.model_validate(
        {**plan.model_dump(include=set(PlanInput.model_fields)), "version": plan.version, "reason": reason}
    )


def test_create_concurrency_version_and_explicit_ending(engine: Engine) -> None:
    def create(_index: int) -> Plan | str:
        try:
            return create_plan(engine, body(), NOW)
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sum(result == "active_plan_exists" for result in results) == 1
    plan = round_plans(engine.store, engine.store.read().id)[0]
    assert get_plan(Store(engine.store.path), plan.id) == plan
    changed = update_plan(engine, plan.id, edit(plan))
    with pytest.raises(DomainError, match="另一设备"):
        _ = update_plan(engine, plan.id, edit(plan, "过期编辑"))
    ended = update_plan(engine, plan.id, edit(changed).model_copy(update={"status": "ended"}))
    assert ended.status == "ended"
    with pytest.raises(DomainError, match="只读"):
        _ = update_plan(engine, plan.id, edit(ended).model_copy(update={"status": "active"}))
    assert create_plan(engine, body(), NOW).id != plan.id
    assert len(round_plans(engine.store, plan.round_id)) == 2


def test_order_snapshot_atomic_revision_and_idempotent_retry(engine: Engine) -> None:
    plan = create_plan(engine, body(), NOW)
    order_input = request().model_copy(update={"plan_id": plan.id, "plan_version": plan.version})
    _ = engine.preview(order_input, NOW)
    order = engine.place(order_input, NOW)
    assert order.plan_snapshot is not None
    assert order.plan_snapshot.reason == "先记录判断"
    changed = update_plan(engine, plan.id, edit(plan))
    assert engine.place(order_input, NOW).id == order.id
    saved = Store(engine.store.path).read().orders[0]
    assert saved.plan_snapshot == order.plan_snapshot
    assert saved.plan_snapshot is not None
    assert changed.reason != saved.plan_snapshot.reason
    before = engine.store.read()
    with pytest.raises(DomainError, match="重新载入"):
        _ = engine.place(order_input.model_copy(update={"request_id": str(uuid4())}), NOW)
    assert engine.store.read() == before
    with pytest.raises(DomainError, match="证券不一致"):
        _ = engine.preview(order_input.model_copy(update={"symbol": "AAPL", "plan_version": changed.version}), NOW)


def test_revision_change_during_quote_fetch_rejects_without_reservation(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = create_plan(engine, body(), NOW)
    original = Engine.prepare

    def editing_prepare(
        instance: Engine, item: OrderInput, at: datetime
    ) -> tuple[Quote, FxRate | None, Decimal | None]:
        result = original(instance, item, at)
        _ = update_plan(engine, plan.id, edit(plan))
        return result

    monkeypatch.setattr(Engine, "prepare", editing_prepare)
    with pytest.raises(DomainError, match="重新载入"):
        _ = engine.place(request().model_copy(update={"plan_id": plan.id, "plan_version": 1}), NOW)
    assert engine.store.read().orders == []
    assert engine.store.read().ledger == []


def test_archive_round_plan_isolation_and_snapshot_legacy_migration(engine: Engine) -> None:
    legacy = engine.place(request(), NOW)
    engine.process(legacy.symbol, (sample(engine, legacy),), NOW + timedelta(minutes=2))
    with engine.store.transaction() as connection:
        old_state = engine.store.load(connection)
        data = old_state.model_dump_json(exclude={"orders": {"__all__": {"plan_id", "plan_version", "plan_snapshot"}}})
        _ = connection.execute("UPDATE rounds SET data=? WHERE active=1", (data,))
        payload = request().model_copy(update={"request_id": legacy.request_id})
        _ = connection.execute(
            "UPDATE requests SET payload=? WHERE id=?",
            (payload.model_dump_json(exclude={"plan_id", "plan_version"}), legacy.request_id),
        )
    before = engine.store.read()
    for _ in range(2):
        assert Store(engine.store.path).read() == before
    assert engine.place(payload, NOW).id == legacy.id
    assert engine.store.read().orders[0].plan_snapshot is None
    plan = create_plan(engine, body(), NOW)
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    assert round_plans(engine.store, engine.store.read().id) == []
    assert get_plan(engine.store, plan.id).round_id == before.id
    with pytest.raises(DomainError, match="归档"):
        _ = update_plan(engine, plan.id, edit(plan))
    with pytest.raises(DomainError, match="归档"):
        _ = engine.preview(request().model_copy(update={"plan_id": plan.id, "plan_version": 1}), NOW)
    assert engine.store.read_round(before.id).cash == before.cash


def test_plan_review_actual_fills_partial_sale_cancel_notes_and_missing_profit(engine: Engine) -> None:
    plan = create_plan(engine, body(), NOW)
    buy = engine.place(request().model_copy(update={"quantity": 200, "plan_id": plan.id, "plan_version": 1}), NOW)
    engine.process(buy.symbol, (sample(engine, buy),), NOW + timedelta(minutes=2))
    later = NOW + timedelta(days=1)
    engine.provider = ControlledProvider(quote_time=later, history_day=NOW.date())
    sell = engine.place(request().model_copy(update={"side": "sell", "plan_id": plan.id, "plan_version": 1}), later)
    engine.process(sell.symbol, (sample(engine, sell, "100"),), later + timedelta(minutes=2))
    review = reviews(engine.store, engine.store.read(), later)[0]
    assert review.status == "pending"
    assert (review.bought_quantity, review.sold_quantity, review.remaining_quantity, review.account_quantity) == (
        200,
        100,
        100,
        100,
    )
    assert "部分卖出" in review.actual_status
    assert review.plan is not None
    assert review.plan.status == "active"
    assert review.realized_pnl == Decimal("90.05")
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders.append(state.orders[-1].model_copy(update={"id": str(uuid4()), "realized_pnl": None}))
        state.journal.append(Journal(id=str(uuid4()), plan_id=plan.id, text="部分卖出复盘", created_at=later))
        engine.store.save(connection, state)
    summary = reviews(engine.store, engine.store.read(), later)[0]
    assert summary.status == "pending"
    assert summary.realized_pnl is None
    assert len(summary.notes) == 1


def test_cancelled_unfilled_is_not_pending_profitable_review(engine: Engine) -> None:
    opening = NOW - timedelta(hours=1)
    engine.provider = ControlledProvider(quote_time=opening - timedelta(minutes=1))
    plan = create_plan(engine, body().model_copy(update={"review_date": None}), opening)
    order = engine.place(request().model_copy(update={"plan_id": plan.id, "plan_version": 1}), opening)
    _ = engine.cancel(order.id, opening)
    review = reviews(engine.store, engine.store.read(), opening)[0]
    assert review.status == "not_due"
    assert review.filled_count == 0
    assert review.realized_pnl is None
    assert "撤单判断" in review.actual_status


def test_saved_observation_can_have_plan_before_trading_activation(engine: Engine) -> None:
    from app.discovery_models import ObservationInstrument  # noqa: PLC0415 — 此场景独立的观察标的边界。

    observed = ObservationInstrument(
        symbol="OBSERVE", name="受控观察证券", market="US", currency="USD", kind="stock", provider_code="usOBSERVE"
    )
    with engine.store.transaction() as connection:
        _ = connection.execute("INSERT INTO observations VALUES(?,?)", (observed.symbol, observed.model_dump_json()))
    plan = create_plan(engine, body().model_copy(update={"symbol": observed.symbol, "direction": "watch"}), NOW)
    assert get_plan(Store(engine.store.path), plan.id).currency == "USD"
    assert all(item.symbol != observed.symbol for item in engine.catalog)
