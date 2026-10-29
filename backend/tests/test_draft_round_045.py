"""再下单草稿绑定轮次：跨设备真实HTTP、网络阻塞与成功幂等优先。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from app.api_models import ResetInput
from app.engine import Engine
from app.main import create_app
from app.models import OrderInput, OrderView
from app.operations import reset
from app.storage import read_rows
from tests.round_support import BarrierProvider, reset_http, set_clock
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request


@pytest.mark.parametrize("route", ["/api/orders/preview", "/api/orders/size", "/api/orders"])
def test_draft_after_preflight_reset_rejected(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
) -> None:
    set_clock(monkeypatch)
    old_round = engine.store.read().id
    payload = request().model_copy(update={"expected_round_id": old_round})
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        for http in (first, second):
            assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        preview = first.post(url + "/api/orders/preview", json=payload.model_dump(mode="json"))
        assert preview.status_code == 200
        checked = first.get(url + "/api/portfolio")
        assert checked.json()["round_id"] == old_round
        reset_http(second, url)
        before = engine.store.read()
        response = first.post(url + route, json=payload.model_dump(mode="json"))
        assert response.status_code == 409
        assert "round_changed" in response.text
        assert engine.store.read() == before
        assert not before.orders
        _ = (tmp_path / "race.json").write_text(
            json.dumps(
                {
                    "preview": preview.status_code,
                    "checked_round": old_round,
                    "new_round": before.id,
                    "route": route,
                    "status": response.status_code,
                    "response": response.json(),
                    "new_orders": len(before.orders),
                },
                ensure_ascii=False,
            )
        )


@pytest.mark.parametrize("route", ["/api/orders/preview", "/api/orders/size", "/api/orders"])
def test_draft_reset_during_network_rejected(engine: Engine, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    set_clock(monkeypatch)
    payload = request().model_copy(update={"expected_round_id": engine.store.read().id})
    provider = BarrierProvider()
    engine.provider = provider
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        for http in (first, second):
            assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.post, url + route, json=payload.model_dump(mode="json"))
            try:
                assert provider.entered.wait(5)
                reset_http(second, url)
                current = engine.store.read()
            finally:
                provider.release.set()
            response = pending.result(5)
        assert response.status_code == 409
        assert "round_changed" in response.text
        assert engine.store.read() == current


def test_bound_request_retry_after_reset_returns_original_and_no_persistent_field(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_clock(monkeypatch)
    payload = request().model_copy(update={"expected_round_id": engine.store.read().id})
    order = engine.place(payload, NOW)
    final = engine.cancel(order.id, NOW)
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    current = engine.store.read()
    engine.provider = ControlledProvider(fx_available=False)
    with engine.store.transaction() as connection:
        raw_before = read_rows(connection, "SELECT data FROM rounds ORDER BY rowid")
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        result = http.post(url + "/api/orders", json=payload.model_dump(mode="json"))
        assert result.status_code == 200
        returned = OrderView.model_validate_json(result.content)
        assert returned.model_dump(exclude={"first_candidate_interval"}) == final.model_dump()
        conflict = http.post(
            url + "/api/orders", json=payload.model_copy(update={"quantity": 200}).model_dump(mode="json")
        )
        assert conflict.status_code == 409
    assert engine.store.read() == current
    with engine.store.transaction() as connection:
        assert read_rows(connection, "SELECT data FROM rounds ORDER BY rowid") == raw_before
    assert "expected_round_id" not in final.model_dump_json()
    assert all("expected_round_id" not in row[0] for row in raw_before)


def test_old_request_without_round_field_still_retries(engine: Engine) -> None:
    payload = request()
    old = payload.model_dump_json(exclude={"expected_round_id"})
    parsed = OrderInput.model_validate_json(old)
    assert parsed.expected_round_id is None
    order = engine.place(parsed, NOW)
    with engine.store.transaction() as connection:
        _ = connection.execute("UPDATE requests SET payload=? WHERE id=?", (old, parsed.request_id))
    assert engine.place(parsed, NOW) == order
    assert len(engine.store.read().orders) == 1
