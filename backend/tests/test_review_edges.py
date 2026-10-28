"""日期跨日、归属边界与精确收益的受控真实账本回归。"""

from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from uuid import uuid4

import pytest

from app import review_conclusions
from app.api_models import ResetInput
from app.engine import Engine
from app.models import DomainError, Journal
from app.operations import reset
from app.plan_alerts import alerts, due_alert, read_alert
from app.plan_reviews import reviews
from app.plans import create_plan, update_plan
from app.review_conclusions import detail, save_conclusion
from app.review_repository import read_context
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_plans import body, edit
from tests.test_review_conclusions import submission


class BeforeMidnight:
    @staticmethod
    def now(_tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 22, 15, 59, tzinfo=UTC)


class AfterMidnight:
    @staticmethod
    def now(_tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 22, 16, 0, tzinfo=UTC)


def test_cross_midnight_stale_coverage_and_no_changes_from_nonfills(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = create_plan(engine, body().model_copy(update={"review_date": NOW.date() + timedelta(days=1)}), NOW)
    monkeypatch.setattr(review_conclusions, "datetime", BeforeMidnight)
    payload = submission(engine, plan.id)
    saved = save_conclusion(engine.store, ("plan", plan.id), payload)
    assert saved.review.review_state == "reviewed"
    opening = NOW - timedelta(hours=1)
    engine.provider = ControlledProvider(quote_time=opening - timedelta(minutes=1))
    order = engine.place(request().model_copy(update={"plan_id": plan.id, "plan_version": 1}), opening)
    _ = engine.cancel(order.id, opening)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.journal.append(Journal(id=str(uuid4()), plan_id=plan.id, text="普通笔记", created_at=NOW))
        engine.store.save(connection, state)
    due_alert(engine.store, plan, AfterMidnight.now())
    alert = alerts(engine.store, plan.round_id)[0]
    _ = read_alert(engine.store, alert.id, NOW)
    assert reviews(engine.store, engine.store.read(), BeforeMidnight.now())[0].review_state == "reviewed"
    stale = submission(engine, plan.id)
    monkeypatch.setattr(review_conclusions, "datetime", AfterMidnight)
    with pytest.raises(DomainError, match="事实已变化"):
        _ = save_conclusion(engine.store, ("plan", plan.id), stale)
    current = detail(read_context(engine.store), ("plan", plan.id), AfterMidnight.now())
    assert current.review.review_state == "needs_update"
    assert current.review.update_reasons == ["已到新的约定复盘日期"]


def test_ended_plan_allowed_but_linked_order_cross_round_and_archive_rejected(engine: Engine) -> None:
    plan = create_plan(engine, body(), NOW)
    order = engine.place(request().model_copy(update={"plan_id": plan.id, "plan_version": 1}), NOW)
    with pytest.raises(DomainError, match="对应计划"):
        _ = detail(read_context(engine.store), ("order", order.id), NOW)
    _ = update_plan(engine, plan.id, edit(plan).model_copy(update={"status": "ended"}))
    assert (
        save_conclusion(engine.store, ("plan", plan.id), submission(engine, plan.id)).review.review_state == "reviewed"
    )
    stale = submission(engine, plan.id)
    engine.process(order.symbol, (sample(engine, order),), NOW + timedelta(minutes=6))
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    with pytest.raises(DomainError, match="归档"):
        _ = save_conclusion(engine.store, ("plan", plan.id), stale)
    with pytest.raises(DomainError, match="不存在"):
        _ = save_conclusion(engine.store, ("plan", plan.id), stale.model_copy(update={"round_id": uuid4()}))
    with pytest.raises(DomainError, match="不存在"):
        _ = detail(read_context(engine.store), ("plan", plan.id), NOW)


def test_exact_saved_sale_profit_partial_position_and_sell_old_holding(engine: Engine) -> None:
    engine.provider = ControlledProvider(price=Decimal(10))
    plan = create_plan(engine, body(), NOW)
    buy = engine.place(
        request().model_copy(
            update={"quantity": 200, "limit_price": Decimal(10), "plan_id": plan.id, "plan_version": 1}
        ),
        NOW,
    )
    engine.process(buy.symbol, (sample(engine, buy, "10"),), NOW + timedelta(minutes=6))
    later = NOW + timedelta(days=1)
    engine.provider = ControlledProvider(quote_time=later, price=Decimal(12), history_day=NOW.date())
    sell = engine.place(
        request().model_copy(
            update={"side": "sell", "limit_price": Decimal(12), "plan_id": plan.id, "plan_version": 1}
        ),
        later,
    )
    engine.process(sell.symbol, (sample(engine, sell, "12"),), later + timedelta(minutes=6))
    summary = reviews(engine.store, engine.store.read(), later)[0]
    assert (summary.remaining_quantity, summary.account_quantity) == (100, 100)
    assert summary.realized_pnl == Decimal("198.90")
    another = create_plan(engine, body().model_copy(update={"status": "draft"}), NOW)
    sell_old = engine.place(
        request().model_copy(
            update={"side": "sell", "limit_price": Decimal(12), "plan_id": another.id, "plan_version": 1}
        ),
        later + timedelta(minutes=10),
    )
    engine.process(sell_old.symbol, (sample(engine, sell_old, "12"),), later + timedelta(minutes=16))
    summary = detail(read_context(engine.store), ("plan", another.id), later).review
    assert summary.remaining_quantity == -100
    assert summary.account_quantity == 0
    assert summary.realized_pnl == Decimal("198.90")


def test_lock_wait_crosses_midnight_and_checks_time_after_acquiring_transaction(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Generator  # noqa: PLC0415 — 本场景的真实锁等待设施。
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    from contextlib import contextmanager  # noqa: PLC0415
    from sqlite3 import Connection  # noqa: PLC0415
    from threading import Event  # noqa: PLC0415

    from app.storage import Store  # noqa: PLC0415

    plan = create_plan(engine, body().model_copy(update={"review_date": NOW.date() + timedelta(days=1)}), NOW)
    payload = submission(engine, plan.id)
    current_time = [BeforeMidnight.now()]
    attempting = Event()
    original_transaction = Store.transaction

    class LockClock:
        @staticmethod
        def now(_tz: tzinfo | None = None) -> datetime:
            return current_time[0]

    @contextmanager
    def observed_transaction(store: Store) -> Generator[Connection]:
        attempting.set()
        with original_transaction(store) as connection:
            yield connection

    def submit() -> str:
        try:
            return save_conclusion(engine.store, ("plan", plan.id), payload).review.review_state
        except DomainError as error:
            return error.code

    monkeypatch.setattr(review_conclusions, "datetime", LockClock)
    monkeypatch.setattr(Store, "transaction", observed_transaction)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with original_transaction(engine.store):
            future = pool.submit(submit)
            assert attempting.wait(timeout=5)
            current_time[0] = AfterMidnight.now()
        assert future.result(timeout=5) == "review_conflict"
    assert read_context(engine.store).conclusions == []
