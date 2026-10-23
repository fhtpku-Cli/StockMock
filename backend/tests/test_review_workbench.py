"""完整主体筛选与超过五百条事实的真实 HTTP 下载验证。"""

import csv
import io
from datetime import timedelta
from uuid import uuid4

from app.api_models import ResetInput
from app.engine import Engine
from app.main import create_app
from app.models import Journal
from app.operations import reset
from app.plan_alerts import due_alert
from app.plan_models import Plan
from app.plans import create_plan, update_plan
from app.review_models import Fact, FactPage, JournalPage, ReviewDetail, ReviewPage
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, request
from tests.test_plans import body, edit


def populate(engine: Engine) -> tuple[Plan, Plan]:
    plans = [create_plan(engine, body().model_copy(update={"status": "draft"}), NOW) for _ in range(31)]
    us = create_plan(engine, body().model_copy(update={"symbol": "AAPL", "status": "draft"}), NOW)
    chosen = plans[0]
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.journal.extend(
            Journal(
                id=str(uuid4()), text=f"第{index}条,中文\n原文", plan_id=chosen.id, created_at=NOW + timedelta(days=1)
            )
            for index in range(507)
        )
        state.journal.append(Journal(id=str(uuid4()), text="=公式", plan_id=us.id, created_at=NOW))
        state.journal.append(Journal(id=str(uuid4()), text="无关联", created_at=NOW))
        engine.store.save(connection, state)
    return chosen, us


def test_complete_counts_cursor_dates_note_market_and_filtered_download(engine: Engine) -> None:
    chosen, us = populate(engine)
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        result = ReviewPage.model_validate_json(http.get(url + "/api/reviews/page?limit=7").content)
        assert result.counts.pending == result.total == 32
        assert len(result.items) == 7
        identifiers = [r.id for r in result.items]
        while result.next_cursor:
            result = ReviewPage.model_validate_json(
                http.get(url + "/api/reviews/page", params={"limit": 7, "cursor": result.next_cursor}).content
            )
            identifiers.extend(r.id for r in result.items)
        assert len(identifiers) == len(set(identifiers)) == 32
        day = (NOW + timedelta(days=1)).date().isoformat()
        params = {"market": "CN", "start_date": day, "end_date": day, "limit": 1}
        selected = ReviewPage.model_validate_json(http.get(url + "/api/reviews/page", params=params).content)
        assert selected.total == 1
        assert selected.items[0].id == chosen.id
        assert http.get(url + "/api/reviews/page?start_date=2030-01-02&end_date=2030-01-01").status_code == 422
        assert http.get(url + "/api/reviews/page?cursor=invalid").status_code == 422
        notes = JournalPage.model_validate_json(http.get(url + "/api/journal/page?market=US").content)
        assert notes.total == 1
        assert notes.items[0].review_subject_id == us.id
        notes = JournalPage.model_validate_json(http.get(url + "/api/journal/page?market=CN&offset=500").content)
        assert notes.total == 507
        assert len(notes.items) == 7
        downloaded = http.get(url + "/api/reviews/export.csv", params=params)
        assert downloaded.status_code == 200
        assert downloaded.content.startswith(b"\xef\xbb\xbf")
        rows = list(csv.DictReader(io.StringIO(downloaded.text.lstrip("\ufeff"))))
        assert len([r for r in rows if r["记录类型"] == "note"]) == 507
        assert all(r["主体ID"] == chosen.id for r in rows)
        us_csv = http.get(url + "/api/reviews/export.csv?market=US").text
        assert "'=公式" in us_csv
        assert "independent_note" not in us_csv
        assert "independent_note" in http.get(url + "/api/reviews/export.csv").text
        first = ReviewPage.model_validate_json(http.get(url + "/api/reviews/page?limit=1").content)
        assert first.next_cursor
        _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
        assert http.get(url + "/api/reviews/page", params={"cursor": first.next_cursor}).status_code == 409


def test_facts_preserve_missing_time_snapshots_and_same_time_order(engine: Engine) -> None:
    plan = create_plan(engine, body(), NOW)
    due_alert(engine.store, plan, NOW)
    changed = update_plan(engine, plan.id, edit(plan))
    order = engine.place(request().model_copy(update={"plan_id": plan.id, "plan_version": changed.version}), NOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders[:] = [
            order.model_copy(
                update={
                    "id": str(uuid4()),
                    "status": "filled",
                    "confirmed_at": None,
                    "filled_at": NOW,
                    "realized_pnl": None,
                }
            )
            for _ in range(507)
        ]
        state.orders.append(order.model_copy(update={"id": str(uuid4()), "status": "expired"}))
        engine.store.save(connection, state)
    before = engine.store.read()
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        path = f"/api/reviews/plan/{plan.id}"
        loaded = ReviewDetail.model_validate_json(http.get(url + path).content)
        assert len(loaded.orders) == 508
        assert len(loaded.coverage.filled_order_ids) == 507
        facts: list[Fact] = []
        total = 0
        for offset in range(0, 1020, 100):
            result = FactPage.model_validate_json(
                http.get(url + path + "/facts", params={"offset": offset, "limit": 100}).content
            )
            facts.extend(result.items)
            total = result.total
        assert len(facts) == len({f.id for f in facts}) == total
        unknown = [f for f in facts if f.occurred_at is None]
        assert len(unknown) == 507
        assert all(f.type == "order_filled" for f in unknown)
        assert not any(f.type == "order_terminal" for f in facts)
        alert = next(f.alert for f in facts if f.type == "alert")
        assert alert
        assert alert.plan_version == 1
        assert loaded.orders[0].plan_snapshot
        assert loaded.orders[0].plan_snapshot.version == 2
        created = next(f for f in facts if f.type == "plan_created")
        assert created.plan is None
        exported = http.get(url + "/api/reviews/export.csv")
        rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
        created_row = next(row for row in rows if row["记录类型"] == "plan_created")
        assert created_row["计划版本"] == "未记录"
        repeat = FactPage.model_validate_json(http.get(url + path + "/facts?limit=100").content)
        assert repeat.items == facts[:100]
    assert engine.store.read() == before
