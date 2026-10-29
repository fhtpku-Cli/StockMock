"""R02：等待投影优先级、旧记录兼容与失败快照发布。"""

import secrets
from datetime import UTC, datetime, timedelta, tzinfo
from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient

from app import trading_routes
from app import worker as worker_module
from app.engine import Engine
from app.main import create_app
from app.marketdata import MarketDataError, Quote
from app.models import Order, OrderView
from app.order_views import WaitingContext, order_view, waiting_view
from app.plan_models import Page
from app.plans import create_plan
from app.storage import read_rows
from app.worker import Worker
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_plans import body as plan_body


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("paused", "paused"),
        ("failure", "source_failing"),
        ("cutoff", "after_cutoff"),
        ("future", "not_active"),
        ("first", "awaiting_first"),
        ("old_sample", "awaiting_first"),
        ("checked", "checked_not_met"),
    ],
)
def test_waiting_reason_matches_current_progress(engine: Engine, scenario: str, expected: str) -> None:
    # Given: 已受理订单的六类现场条件，旧样本仍不代表首候选已处理。
    order = engine.place(request(), NOW)
    now = NOW + timedelta(minutes=6)
    paused: frozenset[str] = frozenset({order.symbol}) if scenario == "paused" else frozenset()
    failures = {order.symbol: NOW} if scenario == "failure" else {}
    if scenario == "cutoff":
        now = order.expires_at
    if scenario == "future":
        now = NOW - timedelta(seconds=1)
    if scenario in {"old_sample", "checked"}:
        order = order.model_copy(
            update={"last_sample_at": NOW + timedelta(minutes=-1 if scenario == "old_sample" else 5)}
        )
    # When: 只读计算当前等待说明。
    view = waiting_view(order, WaitingContext(now, paused, MappingProxyType(failures)))
    # Then: 分类、原始时间均来自服务端。
    assert view is not None
    assert view.code == expected
    assert view.checked_through == order.last_sample_at
    assert view.since == (NOW if scenario == "failure" else None)


@pytest.mark.parametrize(
    ("paused", "failing", "expected"),
    [(True, True, "paused"), (False, True, "source_failing"), (False, False, "after_cutoff")],
)
def test_pause_failure_and_cutoff_priority(engine: Engine, *, paused: bool, failing: bool, expected: str) -> None:
    # Given: 尚未生效的撤单待确认记录同时已到撤单截止。
    original = engine.place(request(), NOW)
    order = original.model_copy(
        update={"status": "cancel_pending", "active_at": NOW + timedelta(hours=1), "cancel_at": NOW}
    )
    context = WaitingContext(
        NOW,
        frozenset({order.symbol}) if paused else frozenset(),
        MappingProxyType({order.symbol: NOW} if failing else {}),
    )
    # When: 同时存在多个候选原因。
    view = waiting_view(order, context)
    # Then: 暂停优先于失败，失败优先于截止，截止优先于未生效。
    assert view is not None
    assert view.code == expected


@pytest.mark.parametrize("status", ["filled", "cancelled", "expired", "terminated"])
def test_terminal_order_has_no_waiting_even_when_paused_and_failing(engine: Engine, status: str) -> None:
    # Given: 已经结束的订单仍有暂存故障信息。
    original = engine.place(request(), NOW)
    order = original.model_copy(update={"status": status})
    context = WaitingContext(NOW, frozenset({order.symbol}), MappingProxyType({order.symbol: NOW}))
    # When / Then: 不给终态补等待原因。
    assert waiting_view(order, context) is None


def test_old_order_without_new_fields_projects_without_persisting(engine: Engine) -> None:
    # Given: 原有持久Order严格模型，不含waiting或新请求字段。
    original = engine.place(request(), NOW)
    old = Order.model_validate_json(original.model_dump_json(exclude={"last_sample_at"}))
    before = engine.store.read().model_dump_json()
    # When: 当前委托和普通复盘/归档投影。
    live = order_view(old, waiting=WaitingContext(NOW, frozenset(), MappingProxyType({})))
    archived = order_view(old)
    # Then: 仅活动请求投影带等待；旧账本保持原样。
    assert live.waiting is not None
    assert live.waiting.code == "awaiting_first"
    assert archived.waiting is None
    assert engine.store.read().model_dump_json() == before
    assert "waiting" not in before


def test_worker_failure_snapshot_is_immutable_copy_and_clears_on_success(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 真实Worker.tick，受控时钟、单次来源故障与恢复。
    order = engine.place(request(), NOW)
    clock = [NOW + timedelta(minutes=1)]
    outage = [True]

    class Clock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    def fetch(provider: ControlledProvider, symbol: str) -> tuple[Quote, ...]:
        if outage[0]:
            raise MarketDataError("controlled_outage", "受控故障")
        return (provider.quote(symbol),)

    monkeypatch.setattr(worker_module, "datetime", Clock)
    monkeypatch.setattr(ControlledProvider, "intraday", fetch)
    worker = Worker(engine)
    worker.tick()
    first = worker.failures
    assert first == {order.symbol: clock[0]}
    assert isinstance(first, MappingProxyType)
    # When: 下一轮成功取得分钟线。
    outage[0] = False
    clock[0] += timedelta(minutes=1)
    worker.tick()
    # Then: 新快照清空，旧快照不被原字典后续修改影响。
    assert not worker.failed_since
    assert not worker.failures
    assert first == {order.symbol: NOW + timedelta(minutes=1)}


def test_trading_http_projects_waiting_but_archive_keeps_none(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 独立测试账户与冻结时钟，旧版请求结构。
    clock = [NOW]

    class Clock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    monkeypatch.setattr(trading_routes, "datetime", Clock)
    plan = create_plan(engine, plan_body(), NOW)
    password = secrets.token_urlsafe(24)
    with TestClient(create_app(engine, password, run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": password}).status_code == 200
        body = (
            request()
            .model_copy(update={"plan_id": plan.id, "plan_version": plan.version})
            .model_dump(mode="json", exclude={"expected_expires_at"})
        )
        # When: 下单、读取与撤单沿真实路由。
        placed = client.post("/api/orders", json=body)
        assert placed.status_code == 200
        placed_body = OrderView.model_validate_json(placed.content)
        assert placed_body.waiting is not None
        assert placed_body.waiting.code == "awaiting_first"
        listed = client.get("/api/orders")
        assert listed.json()[0]["waiting"]["code"] == "awaiting_first"
        archive = client.get(f"/api/rounds/{engine.store.read().id}/orders")
        assert archive.json()["items"][0]["waiting"] is None
        planned = Page[OrderView].model_validate_json(client.get(f"/api/plans/{plan.id}/orders").content)
        assert planned.items[0].waiting is None
        reviewed = client.get(f"/api/reviews/plan/{plan.id}")
        assert reviewed.status_code == 200
        assert reviewed.json()["orders"][0]["waiting"] is None
        clock[0] = NOW + timedelta(minutes=1)
        cancelled = client.post(f"/api/orders/{placed_body.id}/cancel")
        assert cancelled.status_code == 200
        pending = OrderView.model_validate_json(cancelled.content)
        assert pending.status == "cancel_pending"
        assert pending.waiting is not None
        assert pending.waiting.code == "after_cutoff"
    # Then: 请求幂等响应和持久Order没有即时等待字段。
    with engine.store.transaction() as connection:
        saved = read_rows(connection, "SELECT response FROM requests")
    assert len(saved) == 1
    assert "waiting" not in saved[0][0]


def test_overlapping_interval_does_not_claim_first_candidate_processed(engine: Engine) -> None:
    # Given: 10:02生效；首候选区间为10:05–10:10。
    active = NOW + timedelta(minutes=2)
    order = engine.place(request(), active)
    overlap = sample(engine, order, "101", 3, interval_start=NOW)
    # When: 实际处理10:00–10:05，撮合跳过但水位推进至10:05。
    engine.process(order.symbol, (overlap,), NOW + timedelta(minutes=6))
    persisted = engine.store.read().orders[0]
    view = waiting_view(persisted, WaitingContext(NOW + timedelta(minutes=6), frozenset(), MappingProxyType({})))
    # Then: 尚未处理首个完整候选，不误称价格条件未满足。
    assert persisted.last_sample_at == NOW + timedelta(minutes=5)
    assert view is not None
    assert view.code == "awaiting_first"
