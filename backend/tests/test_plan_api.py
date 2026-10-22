"""真实回环 HTTP 的跨设备计划、归档分页和完整安全导出。"""

import csv
import io
from datetime import datetime, tzinfo
from decimal import Decimal
from uuid import uuid4

import pytest

from app import trading_routes
from app.api_models import ResetInput
from app.engine import Engine
from app.main import create_app
from app.models import Entry, Journal, Order
from app.operations import reset
from app.plan_alerts import alerts, due_alert
from app.plan_models import AlertPage, Page, Plan, Review
from app.plans import create_plan, get_plan
from app.storage import Store
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, request
from tests.test_plans import body, edit


class Clock:
    @staticmethod
    def now(_tz: tzinfo | None = None) -> datetime:
        return NOW


def test_two_http_clients_share_plans_conflicts_snapshots_and_notes(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trading_routes, "datetime", Clock)
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        assert first.get(url + "/api/plans").status_code == 401
        assert first.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert second.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        response = first.post(
            url + "/api/plans", content=body().model_dump_json(), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        plan = Plan.model_validate_json(response.content)
        assert Plan.model_validate_json(second.get(url + f"/api/plans/{plan.id}").content) == plan
        order_input = request().model_copy(update={"plan_id": plan.id, "plan_version": 1})
        preview = first.post(url + "/api/orders/preview", json=order_input.model_dump(mode="json"))
        assert preview.status_code == 200
        created = first.post(url + "/api/orders", json=order_input.model_dump(mode="json"))
        assert created.status_code == 200
        order = Order.model_validate_json(created.content)
        assert order.plan_snapshot is not None
        assert order.plan_snapshot.id == plan.id
        changed = second.put(url + f"/api/plans/{plan.id}", json=edit(plan).model_dump(mode="json"))
        assert changed.status_code == 200
        assert first.put(url + f"/api/plans/{plan.id}", json=edit(plan).model_dump(mode="json")).status_code == 409
        persisted = Page[Order].model_validate_json(first.get(url + f"/api/plans/{plan.id}/orders").content)
        assert persisted.items[0].plan_snapshot == order.plan_snapshot
        note = second.post(
            url + "/api/journal", json={"plan_id": plan.id, "order_id": order.id, "text": "保留原判断，等待成交"}
        )
        assert note.status_code == 200
        summaries = Page[Review].model_validate_json(first.get(url + "/api/reviews?status=reviewed").content)
        assert summaries.total == 1
        assert len(summaries.items[0].notes) == 1
        fresh = create_plan(engine, body().model_copy(update={"symbol": "AAPL"}), NOW)
        assert (
            first.post(
                url + "/api/journal", json={"plan_id": fresh.id, "order_id": order.id, "text": "关联错误"}
            ).status_code
            == 422
        )


def test_full_archive_pagination_exports_and_read_only(engine: Engine) -> None:
    plan = create_plan(
        engine, body().model_copy(update={"reason": ' \t=HYPERLINK("unsafe")', "invalidation": "@危险"}), NOW
    )
    due_alert(engine.store, plan, NOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        for index in range(507):
            state.journal.append(Journal(id=str(uuid4()), text=f"第{index}条历史笔记", plan_id=plan.id, created_at=NOW))
            state.ledger.append(
                Entry(
                    id=str(uuid4()),
                    kind="controlled",
                    currency="CNY",
                    amount=Decimal(0),
                    created_at=NOW,
                    note="  =公式" if index == 506 else f"历史流水{index}",
                )
            )
        engine.store.save(connection, state)
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert Page[Plan].model_validate_json(http.get(url + "/api/plans").content).total == 0
        archived = Page[Plan].model_validate_json(http.get(url + f"/api/plans?round_id={plan.round_id}").content)
        assert archived.items[0].id == plan.id
        assert http.put(url + f"/api/plans/{plan.id}", json=edit(plan).model_dump(mode="json")).status_code == 409
        assert http.post(url + "/api/journal", json={"plan_id": plan.id, "text": "不能修改归档"}).status_code == 409
        alert = alerts(engine.store, plan.round_id)[0]
        assert http.post(url + f"/api/alerts/{alert.id}/read").status_code == 409
        for route in ("journal", "ledger"):
            identifiers: list[str] = []
            for offset in range(0, 507, 100):
                response = http.get(url + f"/api/rounds/{plan.round_id}/{route}?offset={offset}&limit=100")
                result = Page[Journal | Entry].model_validate_json(response.content)
                assert result.total == 507
                identifiers.extend(row.id for row in result.items)
            assert len(set(identifiers)) == 507
        assert http.get(url + f"/api/rounds/{plan.round_id}/journal?limit=101").status_code == 422
        for route in ("plans", "reviews", "alerts"):
            exported = http.get(url + f"/api/{route}/export.csv?round_id={plan.round_id}")
            assert exported.status_code == 200
            assert f"{route}.csv" in exported.headers["Content-Disposition"]
            records = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
            assert len(records) >= 2
            if route == "plans":
                assert records[1][7].startswith("' \t=")
                assert records[1][11] == "'@危险"
            if route == "reviews":
                assert "第0条历史笔记" in exported.text
                assert "第506条历史笔记" in exported.text
                assert NOW.isoformat() in exported.text
        ledger = http.get(url + f"/api/rounds/{plan.round_id}/export.csv")
        assert len(list(csv.reader(io.StringIO(ledger.text)))) == 508
        assert "'  =公式" in ledger.text
    assert get_plan(Store(engine.store.path), plan.id).version == 1


def test_alert_http_unread_and_paged_plans(engine: Engine) -> None:
    for _ in range(103):
        _ = create_plan(engine, body().model_copy(update={"status": "draft"}), NOW)
    active = create_plan(engine, body(), NOW)
    due_alert(engine.store, active, NOW)
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        first = Page[Plan].model_validate_json(http.get(url + "/api/plans?limit=100").content)
        tail = Page[Plan].model_validate_json(http.get(url + "/api/plans?limit=100&offset=100").content)
        assert first.total == 104
        assert len({p.id for p in first.items + tail.items}) == 104
        pending = AlertPage.model_validate_json(http.get(url + "/api/alerts?unread_only=true").content)
        assert pending.unread_count == 1
        marked = http.post(url + f"/api/alerts/{pending.items[0].id}/read")
        assert marked.status_code == 200
        assert http.post(url + f"/api/alerts/{pending.items[0].id}/read").content == marked.content
        final = AlertPage.model_validate_json(http.get(url + "/api/alerts?unread_only=true").content)
        assert final.total == final.unread_count == 0


def test_more_than_500_orders_remain_accessible_and_exported(engine: Engine) -> None:
    plan = create_plan(engine, body(), NOW)
    accepted = engine.place(request().model_copy(update={"plan_id": plan.id, "plan_version": 1}), NOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders[:] = [
            accepted.model_copy(update={"id": str(uuid4()), "request_id": str(uuid4()), "status": "cancelled"})
            for _ in range(507)
        ]
        engine.store.save(connection, state)
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        for route in (f"/api/rounds/{plan.round_id}/orders", f"/api/plans/{plan.id}/orders"):
            ids: list[str] = []
            for offset in range(0, 507, 100):
                result = Page[Order].model_validate_json(http.get(url + f"{route}?offset={offset}&limit=100").content)
                assert result.total == 507
                ids.extend(order.id for order in result.items)
            assert len(set(ids)) == 507
        exported = http.get(url + f"/api/reviews/export.csv?round_id={plan.round_id}")
        rows = list(csv.reader(io.StringIO(exported.text)))
        assert len(rows) == 508
        assert all(plan.id in row[16] for row in rows[1:])
