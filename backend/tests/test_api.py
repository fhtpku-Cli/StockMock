"""经 FastAPI HTTP 入口验证登录、账本、幂等与后台行为。"""

import secrets
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

import app.trading_routes as routes_module
from app.api_models import RoundDetail, RoundResult
from app.engine import Engine
from app.main import create_app
from app.models import Entry, Journal, Order, Portfolio, WorkerView
from app.storage import Store
from app.worker import Worker
from tests.test_engine import NOW, ControlledProvider, sample

PASSWORD = secrets.token_urlsafe(24)


def test_private_account_when_unauthenticated(engine: Engine) -> None:
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        response = client.get("/api/portfolio")
        assert response.status_code == 401
        assert client.get("/api/auth/session").json() == {
            "authenticated": False,
            "configured": True,
        }


def test_login_cookie_and_empty_account(engine: Engine) -> None:
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        response = client.post("/api/auth/login", json={"password": PASSWORD})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        account = Portfolio.model_validate_json(client.get("/api/portfolio").content)
        assert account.total_assets_cny == Decimal("1000000.00")
        assert account.pnl_cny == 0
        assert account.positions == []
        assert client.get("/api/ledger").json() == []


def test_password_unconfigured_denies_login(engine: Engine) -> None:
    with TestClient(create_app(engine, "", run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 503


def test_mutation_rejects_cross_origin(engine: Engine) -> None:
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        response = client.post(
            "/api/watchlist",
            json={"symbol": "AAPL"},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert client.get("/api/watchlist").json() == []


def test_watchlist_notes_and_export_round_trip(engine: Engine) -> None:
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        assert client.post("/api/watchlist", json={"symbol": "AAPL"}).status_code == 200
        assert client.get("/api/watchlist").json()[0]["symbol"] == "AAPL"
        assert client.post("/api/journal", json={"text": "先观察，再决策"}).status_code == 200
        assert len(TypeAdapter(list[Journal]).validate_json(client.get("/api/journal").content)) == 1
        assert client.get("/api/export.csv").headers["content-type"].startswith("text/csv")
        _ = client.delete("/api/watchlist/AAPL")
        assert client.get("/api/watchlist").json() == []


def test_http_order_validation_and_worker_without_browser(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    class MarketClock:
        @staticmethod
        def now(_tz: tzinfo | None = None) -> datetime:
            return NOW

    monkeypatch.setattr(routes_module, "datetime", MarketClock)
    now = NOW
    engine.provider = ControlledProvider(quote_time=now - timedelta(minutes=1))
    app = create_app(engine, PASSWORD, run_worker=False)
    with TestClient(app) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        payload = {
            "request_id": str(uuid4()),
            "symbol": "600519.SS",
            "side": "buy",
            "order_type": "limit",
            "quantity": 100,
            "limit_price": "100",
        }
        invalid = {**payload, "quantity": -1}
        assert client.post("/api/orders", json=invalid).status_code == 422
        sized = client.post("/api/orders/size?percent=25", json=payload)
        assert sized.status_code == 200, sized.text
        assert sized.json()["suggested_quantity"] == 2400
        assert client.post("/api/orders/size?percent=13", json=payload).status_code == 422
        response = client.post("/api/orders", json=payload)
        assert response.status_code == 200, response.text
        assert client.post("/api/orders", json=payload).json()["id"] == response.json()["id"]
    # 浏览器已关闭，通过同一真实撮合入口处理可成交时段的完整样本。
    order = engine.store.read().orders[0]
    q = sample(engine, order, "99", 5, interval_start=order.active_at)
    engine.process(order.symbol, (q,), q.market_time + timedelta(seconds=1))
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as restarted:
        _ = restarted.post("/api/auth/login", json={"password": PASSWORD})
        orders = TypeAdapter(list[Order]).validate_json(restarted.get("/api/orders").content)
        assert orders[0].status == "filled"
        assert len(TypeAdapter(list[Entry]).validate_json(restarted.get("/api/ledger").content)) == 2
        assert restarted.get("/api/portfolio").json()["positions"][0]["quantity"] == 100


def test_background_lifespan_runs_without_http_request(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    event = Event()
    observed: list[WorkerView] = []
    original = Worker.tick

    def observed_tick(worker: Worker) -> None:
        original(worker)
        observed.append(worker.view)
        event.set()

    monkeypatch.setattr(Worker, "tick", observed_tick)
    with TestClient(create_app(engine, PASSWORD)):
        assert event.wait(timeout=3)
        assert observed[0].last_run is not None
        assert observed[0].status == "running"


def test_archive_remains_readable_and_exportable_after_restart(engine: Engine) -> None:
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        old_id = Portfolio.model_validate_json(client.get("/api/portfolio").content).round_id
        _ = client.post("/api/journal", json={"text": "归档保留的原始计划"})
        result = client.post("/api/rounds/reset", json={"request_id": str(uuid4()), "confirmation": "重新开始"})
        assert result.status_code == 200
        new_id = RoundResult.model_validate_json(result.content).round_id
    restored = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    with TestClient(create_app(restored, PASSWORD, run_worker=False)) as restarted:
        assert restarted.get(f"/api/rounds/{old_id}").status_code == 401
        _ = restarted.post("/api/auth/login", json={"password": PASSWORD})
        archived = restarted.get(f"/api/rounds/{old_id}")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None
        assert len(RoundDetail.model_validate_json(archived.content).journal) == 1
        assert archived.json()["truncated"] is False
        assert restarted.get("/api/portfolio").json()["round_id"] == new_id
        assert restarted.get("/api/journal").json() == []
        assert restarted.get(f"/api/rounds/{old_id}/export.csv").status_code == 200
        assert restarted.post(f"/api/rounds/{old_id}", json={}).status_code == 405
