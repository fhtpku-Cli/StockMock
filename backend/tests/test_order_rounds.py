"""经真实 HTTP 验证订单归档重试和取行情期间的轮次竞争。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Literal, assert_never

import pytest

from app.engine import Engine
from app.main import create_app
from app.models import Order
from app.plans import create_plan
from app.storage import Store, read_rows
from tests.round_support import BarrierProvider, MarketClock, reset_http, retry_in_new_process, set_clock
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_plans import body


@pytest.mark.parametrize("terminal", ["filled", "cancelled", "terminated"])
@pytest.mark.parametrize("legacy", [False, True])
def test_archived_retry_returns_final_order_after_reload(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    terminal: Literal["filled", "cancelled", "terminated"],
    *,
    legacy: bool,
) -> None:
    # Given: 实际订单进入终态；旧结构库省略当时尚不存在的可选字段。
    set_clock(monkeypatch)
    payload = request()
    order = engine.place(payload, NOW)
    match terminal:
        case "filled":
            engine.process(order.symbol, (sample(engine, order),), NOW + timedelta(minutes=6))
        case "cancelled":
            _ = engine.cancel(order.id, NOW)
        case "terminated":
            engine.process(order.symbol, (), order.expires_at + timedelta(minutes=21))
        case _:
            assert_never(terminal)
    if legacy:
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            data = state.model_dump_json(exclude={"orders": {"__all__": {"plan_id", "plan_version", "plan_snapshot"}}})
            _ = connection.execute("UPDATE rounds SET data=? WHERE id=?", (data, state.id))
            _ = connection.execute(
                "UPDATE requests SET payload=?,response=? WHERE id=?",
                (
                    payload.model_dump_json(exclude={"plan_id", "plan_version"}),
                    order.model_dump_json(exclude={"plan_id", "plan_version", "plan_snapshot"}),
                    payload.request_id,
                ),
            )
    final = engine.store.read().orders[0]
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        reset_http(http, url)
    restarted = Engine(Store(engine.store.path), ControlledProvider(fx_available=False), engine.catalog)
    with restarted.store.transaction() as connection:
        before = read_rows(connection, "SELECT data FROM rounds ORDER BY rowid")
    if legacy and terminal == "filled":
        assert retry_in_new_process(engine.store.path, payload) == final
    # When: 重新加载持久库后，使用相同请求从真实 API 重试。
    with server(create_app(restarted, PASSWORD, run_worker=False)) as url, client() as http:
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        response = http.post(url + "/api/orders", json=payload.model_dump(mode="json"))
        # Then: 返回最终记录；不同请求体仍拒绝，所有轮次原始 JSON 均不写回。
        assert response.status_code == 200
        assert Order.model_validate_json(response.content) == final
        conflict = http.post(
            url + "/api/orders", json=payload.model_copy(update={"quantity": 200}).model_dump(mode="json")
        )
        assert conflict.status_code == 409
    with restarted.store.transaction() as connection:
        assert read_rows(connection, "SELECT data FROM rounds ORDER BY rowid") == before


@pytest.mark.parametrize("linked", [False, True])
def test_order_waiting_for_quote_rejects_round_reset(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, *, linked: bool
) -> None:
    # Given: 请求在供应商处等待，另一设备仍能重开，证明没有跨网络持有写事务。
    set_clock(monkeypatch)
    payload = request()
    if linked:
        plan = create_plan(engine, body(), NOW)
        payload = payload.model_copy(update={"plan_id": plan.id, "plan_version": plan.version})
    provider = BarrierProvider()
    engine.provider = provider
    old_id = engine.store.read().id
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        for http in (first, second):
            _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.post, url + "/api/orders", json=payload.model_dump(mode="json"))
            try:
                assert provider.entered.wait(timeout=5)
                MarketClock.current = NOW + timedelta(minutes=1)
                reset_http(second, url)
                old = engine.store.read_round(old_id)
                current = engine.store.read()
            finally:
                provider.release.set()
            # When: 旧请求在新轮建立后拿到行情并继续提交。
            response = pending.result(timeout=5)
        # Then: 显式轮次冲突，原轮和新轮都没有订单或流水变化。
        assert response.status_code == 409
        assert '"round_changed"' in response.text
        assert engine.store.read_round(old_id) == old
        assert engine.store.read() == current
        with engine.store.transaction() as connection:
            assert not read_rows(connection, "SELECT id FROM requests WHERE id=?", (payload.request_id,))


def test_overlapping_duplicate_returns_saved_final_order_before_round_conflict(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 第一个同 ID 请求等行情；第二个完成订单、终止并归档。
    set_clock(monkeypatch)
    provider = BarrierProvider()
    engine.provider = provider
    payload = request()
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        for http in (first, second):
            _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.post, url + "/api/orders", json=payload.model_dump(mode="json"))
            try:
                assert provider.entered.wait(timeout=5)
                response = second.post(url + "/api/orders", json=payload.model_dump(mode="json"))
                order = Order.model_validate_json(response.content)
                final = engine.cancel(order.id, NOW)
                reset_http(second, url)
                current = engine.store.read()
            finally:
                provider.release.set()
            # When: 第一个请求晚到第二事务，此时已有同 ID 成功记录且轮次改变。
            response = pending.result(timeout=5)
        # Then: 返回旧订单终态，不能误报轮次冲突或新建订单。
        assert response.status_code == 200
        assert Order.model_validate_json(response.content) == final
        assert engine.store.read() == current
