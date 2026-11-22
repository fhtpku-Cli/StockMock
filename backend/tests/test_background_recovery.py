"""真实 lifespan、临时账本和提醒的异常恢复及关闭回归。"""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from threading import Event

import anyio
import pytest
from fastapi.testclient import TestClient

import app.plan_worker as plan_module
import app.worker as trade_module
from app.engine import Engine
from app.main import create_app
from app.marketdata import Quote
from app.models import HealthView, State
from app.plan_alerts import alerts
from app.plan_models import Alert
from app.plan_worker import PlanWorker
from app.plans import create_plan
from app.storage import Store
from app.worker import Worker
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_plans import body


class Clock:
    @staticmethod
    def now(_tz: tzinfo | None = UTC) -> datetime:
        return NOW + timedelta(minutes=6)


def committed_result(engine: Engine, order_id: str, round_id: str) -> tuple[State, list[Alert]]:
    state = Store(engine.store.path).read()
    saved_alerts = alerts(Store(engine.store.path), round_id)
    assert state.orders[0].id == order_id
    assert state.orders[0].status == "filled"
    assert len(state.ledger) == 2
    assert state.lots[0].quantity == 100
    assert state.cash["CNY"] == Decimal("990095.05")
    assert {alert.condition for alert in saved_alerts} == {"review_due", "target"}
    assert len(saved_alerts) == 2
    return state, saved_alerts


def assert_failed_health(client: TestClient, failed_task: str) -> None:
    health = HealthView.model_validate_json(client.get("/api/health").content)
    failed = health if failed_task == "Worker" else health.plan_worker
    healthy = health.plan_worker if failed_task == "Worker" else health
    assert failed.status != "running"
    assert failed.last_run == Clock.now()
    assert healthy.status == "running"


def fail_after_first_write[T](
    tick: Callable[[T], None], calls: Counter[str], name: str, failed_task: str
) -> Callable[[T], None]:
    def run(worker: T) -> None:
        tick(worker)
        calls[name] += 1
        if name == failed_task and calls[name] == 1:
            raise RuntimeError("private-provider-credential")

    return run


@pytest.mark.parametrize("failed_task", ["Worker", "PlanWorker"])
def test_lifespan_recovers_after_committed_write(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failed_task: str
) -> None:
    order = engine.place(request(), NOW)
    plan = create_plan(engine, body().model_copy(update={"target_price": Decimal(99)}), NOW)
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=5), price=Decimal(99))
    engine.plan_provider = engine.provider
    calls = Counter({"Worker": 0, "PlanWorker": 0})
    first = {name: Event() for name in calls}
    resume = {name: Event() for name in calls}
    second = {name: Event() for name in calls}

    async def next_round(_delay: float) -> None:
        task_name = anyio.get_current_task().name
        assert task_name is not None
        name = task_name.split(".")[-2]
        if calls[name] == 1:
            first[name].set()
            assert await anyio.to_thread.run_sync(resume[name].wait, 5)
            await anyio.lowlevel.checkpoint()
        else:
            second[name].set()
            await anyio.sleep_forever()

    monkeypatch.setattr(trade_module, "datetime", Clock)
    monkeypatch.setattr(plan_module, "datetime", Clock)
    monkeypatch.setattr(Worker, "tick", fail_after_first_write(Worker.tick, calls, "Worker", failed_task))
    monkeypatch.setattr(PlanWorker, "tick", fail_after_first_write(PlanWorker.tick, calls, "PlanWorker", failed_task))
    monkeypatch.setattr(anyio, "sleep", next_round)
    with TestClient(create_app(engine, PASSWORD)) as client:
        try:
            assert all(event.wait(5) for event in first.values())
            assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
            assert_failed_health(client, failed_task)
            committed, committed_alerts = committed_result(engine, order.id, plan.round_id)
            other = "PlanWorker" if failed_task == "Worker" else "Worker"
            resume[other].set()
            assert second[other].wait(5)
            assert calls[failed_task] == 1
            resume[failed_task].set()
            assert second[failed_task].wait(5)
            recovered = HealthView.model_validate_json(client.get("/api/health").content)
            assert recovered.status == recovered.plan_worker.status == "running"
            assert Store(engine.store.path).read() == committed
            assert alerts(Store(engine.store.path), plan.round_id) == committed_alerts
        finally:
            for event in resume.values():
                event.set()
    assert calls == {"Worker": 2, "PlanWorker": 2}
    assert "private-provider-credential" not in caplog.text


@pytest.mark.parametrize("recover", [False, True])
def test_unexpected_provider_failure_preserves_timeout_and_recovery(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, *, recover: bool
) -> None:
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=10))
    clock = [NOW + timedelta(minutes=30)]
    outage = [True]
    quote = sample(engine, order, offset=5)

    class AdvancingClock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    def intraday(_provider: ControlledProvider, _symbol: str) -> tuple[Quote, ...]:
        if outage[0]:
            raise RuntimeError("private-provider-credential")
        return (quote,)

    monkeypatch.setattr(trade_module, "datetime", AdvancingClock)
    monkeypatch.setattr(ControlledProvider, "intraday", intraday)
    worker = Worker(engine)
    for minute in range(20):
        clock[0] = NOW + timedelta(minutes=30 + minute)
        worker.tick()
        assert engine.store.read().orders[0].status == "cancel_pending"
        assert worker.failures[order.symbol] == NOW + timedelta(minutes=30)
        assert worker.view.status != "running"
    clock[0] += timedelta(minutes=1)
    outage[0] = not recover
    worker.tick()
    persisted = Store(engine.store.path).read()
    assert persisted.orders[0].status == ("filled" if recover else "terminated")
    assert len(persisted.ledger) == (2 if recover else 0)
    assert len(persisted.lots) == (1 if recover else 0)
    assert "private-provider-credential" not in caplog.text
