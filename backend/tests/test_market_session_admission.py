"""Q03 真实 Engine / SQLite / HTTP 保持原受理；仅行情与时钟受控。"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

import pytest

from app import market_routes, trading_routes
from app.engine import Engine
from app.main import create_app
from app.market_session_models import MarketSessionView
from app.models import OrderView, Preview
from app.storage import Store
from tests.market_session_support import Clock, dump, preserve, receipt
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import ControlledProvider, request


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    time: str
    kind: Literal["limit", "market"]
    error: str | None = None
    symbol: str = "600519.SS"
    active: str | None = None
    paused: bool = False
    stale: bool = False
    poor: bool = False


SCENARIOS: Final = [
    Scenario("cn-normal-market", "2026-10-29T02:00:00Z", "market"),
    Scenario("cn-normal-limit", "2026-10-29T02:00:00Z", "limit"),
    Scenario("cn-lunch-market", "2026-10-29T03:30:00Z", "market", "market_closed"),
    Scenario("cn-lunch-limit", "2026-10-29T03:30:00Z", "limit"),
    Scenario("cn-closing-market", "2026-10-29T06:57:00Z", "market", "market_closed"),
    Scenario("cn-closing-limit", "2026-10-29T06:57:00Z", "limit", "reference_not_closed"),
    Scenario("cn-after-limit", "2026-10-29T07:01:00Z", "limit", active="2026-10-30T01:30:00Z"),
    Scenario("us-early-market", "2026-11-27T18:00:00Z", "market", "market_closed", "AAPL"),
    Scenario("us-early-limit", "2026-11-27T18:00:00Z", "limit", symbol="AAPL", active="2026-11-30T14:30:00Z"),
    Scenario("hk-observation", "2026-10-29T02:00:00Z", "limit", "observation_only", "0700.HK"),
    Scenario("cn-paused", "2026-10-29T02:00:00Z", "limit", "observation_only", paused=True),
    Scenario("cn-stale", "2026-10-29T02:00:00Z", "limit", "quote_stale", stale=True),
    Scenario("cn-poor", "2026-10-29T02:00:00Z", "limit", "insufficient_funds", poor=True),
]


def make_engine(tmp_path: Path, scenario: Scenario, now: datetime) -> Engine:
    reference_day = datetime.fromisoformat("2026-10-29" if scenario.name == "cn-after-limit" else "2026-10-28").date()
    provider = ControlledProvider(
        quote_time=now - timedelta(minutes=21 if scenario.stale else 0), history_day=reference_day
    )
    engine = Engine(Store(tmp_path / "ledger.sqlite3"), provider, clock=lambda: now)
    if scenario.paused:
        engine.paused = frozenset({scenario.symbol})
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(1000000)
        if scenario.poor:
            state.cash["CNY"] = Decimal(1)
        engine.store.save(connection, state)
    return engine


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_admission_stays_same_when_session_is_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
) -> None:
    # Given: 全新隔离库和合格受控行情，当前日线只在实际收盘后取得。
    now = datetime.fromisoformat(scenario.time)
    monkeypatch.setattr(Clock, "current", now)
    monkeypatch.setattr(market_routes, "datetime", Clock)
    monkeypatch.setattr(trading_routes, "datetime", Clock)
    engine = make_engine(tmp_path, scenario, now)
    body = request().model_copy(
        update={
            "symbol": scenario.symbol,
            "order_type": scenario.kind,
            "limit_price": Decimal(100) if scenario.kind == "limit" else None,
        }
    )
    market = "US" if scenario.symbol == "AAPL" else "HK" if scenario.symbol == "0700.HK" else "CN"
    before = dump(engine.store.path)
    receipt(f"q03-{scenario.name}-before.sql", before)
    preserve(f"q03-{scenario.name}-before.sqlite3", engine.store.path)
    rows: list[str] = [
        json.dumps({"request": body.model_dump(mode="json"), "now": scenario.time, "source": "controlled-test"})
    ]
    # When: 同一真实 HTTP 预览，在时段 GET 前后比较，再提交业务 POST。
    with (
        server(
            create_app(engine, PASSWORD, run_worker=False), port=int(os.getenv("STOCKMOCK_SESSION_PORT", "0"))
        ) as url,
        client() as http,
    ):
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        first = http.post(url + "/api/orders/preview", json=body.model_dump(mode="json"))
        view_response = http.get(url + f"/api/markets/{market}/session")
        second = http.post(url + "/api/orders/preview", json=body.model_dump(mode="json"))
        rows.extend(
            [
                f"URL {url}",
                f"preview_before {first.status_code} {first.text}",
                f"session {view_response.status_code} {view_response.text}",
                f"preview_after {second.status_code} {second.text}",
            ]
        )
        receipt(f"q03-{scenario.name}-http.jsonl", "\n".join(rows))
        assert first.status_code == second.status_code
        assert first.content == second.content
        assert view_response.status_code == 200
        _ = MarketSessionView.model_validate_json(view_response.content)
        assert dump(engine.store.path) == before
        placed = http.post(url + "/api/orders", json=body.model_dump(mode="json"))
        rows.append(f"place {placed.status_code} {placed.text}")
        receipt(f"q03-{scenario.name}-http.jsonl", "\n".join(rows))
    # Then: 保留既有错误码/资金/预挂生效时间，GET 不影响原受理。
    after = dump(engine.store.path)
    receipt(f"q03-{scenario.name}-after.sql", after)
    preserve(f"q03-{scenario.name}-after.sqlite3", engine.store.path)
    if scenario.error is not None:
        assert first.status_code == placed.status_code == 409
        assert first.json()["detail"]["code"] == placed.json()["detail"]["code"] == scenario.error
        assert before == after
    else:
        assert first.status_code == placed.status_code == 200
        preview = Preview.model_validate_json(first.content)
        order = OrderView.model_validate_json(placed.content)
        assert order.status == "accepted"
        assert order.active_at == preview.active_at
        if scenario.active:
            assert order.active_at == datetime.fromisoformat(scenario.active)
        assert len(engine.store.read().orders) == 1
    receipt(
        f"q03-{scenario.name}-result.json",
        json.dumps(
            {
                "preview_unchanged": True,
                "get_read_only": True,
                "expected_error": scenario.error,
                "order_status": placed.status_code,
                "server_stopped": True,
            }
        ),
    )
