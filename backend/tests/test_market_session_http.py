"""Q02 真实认证 HTTP / SQLite 只读证据，固定端口限隔离验收。"""

import json
import os
from datetime import datetime

import pytest

from app import market_routes
from app.discovery import Discovery
from app.engine import Engine
from app.main import create_app
from app.market_session_models import MarketSessionView
from app.storage import Store
from tests.market_session_support import Clock, dump, preserve, receipt
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import ControlledProvider
from tests.test_market_session import cases


def test_real_http_when_all_boundaries_are_read_only(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 全新真实 SQLite，无后台任务；禁止查询路径触碰其他服务。
    app = create_app(engine, PASSWORD, run_worker=False)
    before = dump(engine.store.path)
    receipt("q02-before.sql", before)
    preserve("q02-before.sqlite3", engine.store.path)
    monkeypatch.setattr(market_routes, "datetime", Clock)
    forbidden: list[str] = []

    def fail(*_args: str, **_kwargs: str) -> None:
        forbidden.append("called")
        pytest.fail("时段 GET 调用了 Provider/Discovery/Store")

    for owner, names in [
        (Store, ("read", "load", "save", "transaction")),
        (Discovery, ("lookup", "search", "quote", "history")),
        (ControlledProvider, ("quote", "history", "intraday", "fx_rates")),
    ]:
        for name in names:
            monkeypatch.setattr(owner, name, fail)
    rows: list[str] = []
    # When: 真实 Uvicorn 回环鉴权后通过完整固定边界矩阵。
    with server(app, port=int(os.getenv("STOCKMOCK_SESSION_PORT", "0"))) as url, client() as http:
        denied = http.get(url + "/api/markets/CN/session")
        assert denied.status_code == 401
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        invalid = http.get(url + "/api/markets/JP/session")
        assert invalid.status_code == 422
        rows.extend(
            [
                f"URL {url}",
                f"unauthenticated {denied.status_code} {denied.text}",
                f"invalid {invalid.status_code} {invalid.text}",
            ]
        )
        for market, now, phase, current, following in cases():
            monkeypatch.setattr(Clock, "current", now)
            monkeypatch.setattr(Clock, "calls", 0)
            response = http.get(url + f"/api/markets/{market}/session", params={"now": "2000-01-01T00:00:00Z"})
            rows.append(
                json.dumps(
                    {
                        "method": "GET",
                        "market": market,
                        "time": now.isoformat(),
                        "status": response.status_code,
                        "cache_control": response.headers.get("cache-control"),
                        "body": response.text,
                    }
                )
            )
            receipt("q02-http.jsonl", "\n".join(rows))
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            view = MarketSessionView.model_validate_json(response.content)
            assert Clock.calls == 1
            assert view.as_of == now
            assert view.phase == phase
            assert ((view.current_period.start, view.current_period.end) if view.current_period else None) == current
            assert (view.next_period.start if view.next_period else None) == following
        monkeypatch.setattr(Clock, "current", datetime.fromisoformat("2027-01-01T02:00:00Z"))
        unknown = http.get(url + "/api/markets/CN/session")
        assert MarketSessionView.model_validate_json(unknown.content).phase == "unknown"
        monkeypatch.setattr(Clock, "current", datetime.fromisoformat("2026-10-29T02:00:00Z"))
        restored = http.get(url + "/api/markets/CN/session")
        assert MarketSessionView.model_validate_json(restored.content).phase == "supported"
        rows.extend(
            [f"unknown {unknown.status_code} {unknown.text}", f"restored {restored.status_code} {restored.text}"]
        )
    # Then: 所有 schema 和原始行完全相同，且禁用依赖从未被调用。
    after = dump(engine.store.path)
    assert before == after
    assert not forbidden
    receipt("q02-http.jsonl", "\n".join(rows))
    receipt("q02-after.sql", after)
    preserve("q02-after.sqlite3", engine.store.path)
    receipt(
        "q02-read-only.json",
        json.dumps(
            {
                "schema_and_rows_equal": before == after,
                "forbidden_calls": forbidden,
                "requests_with_single_now": len(cases()),
                "server_stopped": True,
            }
        ),
    )
