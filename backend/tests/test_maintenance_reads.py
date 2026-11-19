"""本地身份与动态证券复盘的 HTTP 行为回归。"""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app import main, review_routes
from app.activated_registry import RuleProvider
from app.discovery import Discovery, persist_observation
from app.discovery_models import ObservationInstrument
from app.engine import Engine
from app.main import create_app
from app.marketdata import MarketDataError
from app.models import OrderView
from app.review_models import ReviewDetail
from app.storage import Store
from app.worker import Worker
from tests.admission_support import Clock
from tests.test_admission import candidate, inject, isolated
from tests.test_api import PASSWORD
from tests.test_engine import NOW, request


def test_local_identity_survives_watch_removal_and_restart_without_search(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 已持久化的观察身份，实时搜索不可用。
    observed = ObservationInstrument(
        symbol="688981.SS", name="中芯国际", market="CN", currency="CNY", kind="stock", provider_code="sh688981"
    )
    with engine.store.transaction() as connection:
        persist_observation(connection, observed)

    def unavailable(_self: Discovery, _query: str) -> tuple[ObservationInstrument, ...]:
        raise MarketDataError("offline", "受控离线")

    monkeypatch.setattr(Discovery, "search", unavailable)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as http:
        assert http.get("/api/instruments/688981.SS").status_code == 401
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert http.post("/api/watchlist", json={"symbol": observed.symbol}).status_code == 200
        assert http.delete("/api/watchlist/688981.SS").status_code == 200
    # When: 新实例从同一隔离库读取已经移出自选的证券。
    reopened = Engine(Store(engine.store.path), engine.provider)
    with TestClient(create_app(reopened, PASSWORD, run_worker=False)) as http:
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        found = http.get("/api/instruments/688981.SS")
        # Then: 完整身份仍可取，未知代码404，基础身份可取，搜索路由仍单独处理。
        assert found.status_code == 200
        assert ObservationInstrument.model_validate_json(found.content) == observed
        assert http.get("/api/instruments/AAPL").json()["symbol"] == "AAPL"
        assert http.get("/api/instruments/UNKNOWN").status_code == 404
        assert http.get("/api/instruments/search?q=UNKNOWN").status_code == 503


@pytest.mark.parametrize("progress", ["waiting", "checked", "failure", "paused"])
def test_dynamic_order_review_matches_order_entry_with_real_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, progress: str
) -> None:
    # Given: 正常启用动态证券并通过API下单。
    rule = candidate()
    inject(monkeypatch, rule)
    monkeypatch.setattr(review_routes, "datetime", Clock)
    engine = isolated(tmp_path)
    worker = Worker(engine)

    def application_worker(_engine: Engine) -> Worker:
        return worker

    monkeypatch.setattr(main, "Worker", application_worker)
    app = create_app(engine, PASSWORD, run_worker=False)
    with TestClient(app) as http:
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert http.post(f"/api/instruments/{rule.symbol}/enable", json={}).status_code == 200
        assert http.get(f"/api/instruments/{rule.symbol}").json()["tradable"]
        result = http.post(
            "/api/orders",
            json={
                "request_id": str(uuid4()),
                "symbol": rule.symbol,
                "side": "buy",
                "order_type": "limit",
                "quantity": 100,
                "limit_price": "99",
            },
        )
        assert result.status_code == 200, result.text
        order_id = OrderView.model_validate_json(result.content).id
        if progress in {"checked", "failure", "paused"}:
            Clock.current = NOW + timedelta(minutes=5)
            if progress == "failure":

                def failed(_self: RuleProvider, _symbol: str) -> tuple[()]:
                    raise MarketDataError("offline", "受控故障")

                monkeypatch.setattr(RuleProvider, "intraday", failed)
            if progress == "paused":
                engine.paused = frozenset({rule.symbol})
            worker.tick()
        # When: 同一委托从委托列表和复盘入口读取。
        listed = TypeAdapter(list[OrderView]).validate_json(http.get("/api/orders").content)[0]
        reviewed = http.get(f"/api/reviews/order/{order_id}")
        # Then: 动态规则计算出候选区间，当前后台状态一致。
        assert reviewed.status_code == 200, reviewed.text
        view = ReviewDetail.model_validate_json(reviewed.content).orders[0]
        assert view.first_candidate_interval == listed.first_candidate_interval
        assert view.first_candidate_interval.status == "available"
        assert view.waiting == listed.waiting
        expected = {
            "waiting": "awaiting_first",
            "checked": "checked_not_met",
            "failure": "source_failing",
            "paused": "paused",
        }
        assert view.waiting is not None
        assert view.waiting.code == expected[progress]


def test_archived_unknown_rule_order_stays_readable_without_live_waiting(engine: Engine) -> None:
    # Given: 迁移留下的缺规则旧单归档，保留原有委托字段。
    order = engine.place(request(), NOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders[0] = order.model_copy(update={"symbol": "MISSING", "status": "cancelled"})
        engine.store.save(connection, state.model_copy(update={"archived_at": NOW + timedelta(days=1)}))
    # When: 从真实复盘接口读取历史主体。
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as http:
        assert http.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        response = http.get(f"/api/reviews/order/{order.id}?round_id={state.id}")
    # Then: 旧单可读，规则未知，归档没有当前等待状态。
    assert response.status_code == 200, response.text
    assert response.json()["read_only"]
    view = ReviewDetail.model_validate_json(response.content).orders[0]
    assert view.first_candidate_interval.status == "unknown"
    assert view.waiting is None
