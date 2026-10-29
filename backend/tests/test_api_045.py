"""0.4.5 真实回环HTTP：日线缓存刷新、受理、只读投影和快照CSV。"""

import csv
import io
import json
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import ClassVar, override

import pytest

import app.marketdata as md
from app import trading_routes
from app.engine import Engine
from app.main import create_app
from app.marketdata import MarketDataClient
from app.models import OrderView
from app.snapshots import capture, snapshots
from app.storage import Store
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request
from tests.test_personal import held
from tests.test_snapshots import UnavailableProvider


class Clock:
    current: ClassVar[datetime] = datetime(2026, 10, 29, 6, 30, tzinfo=UTC)
    ticks: ClassVar[float] = 1000

    @classmethod
    def now(cls, _tz: tzinfo | None = None) -> datetime:
        return cls.current


def test_actual_http_refresh_and_api_acceptance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history_calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/history":
                if Clock.current.hour >= 7:
                    Clock.current += timedelta(seconds=3)
                price = "100" if Clock.current.hour < 7 else "105"
                body = json.dumps(
                    {"code": 0, "data": {"sh600519": {"day": [["2026-10-29", price, price, price, price, "1000"]]}}}
                ).encode()
                history_calls.append(Clock.current.isoformat())
            else:
                body = (
                    b'var _data=([{"day":"2026-10-29 14:55:00","open":"105","high":"105",'
                    b'"low":"105","close":"105","volume":"1000"}]);'
                )
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: str) -> None:
            """受控服务不打印请求。"""

    monkeypatch.setattr(Clock, "current", datetime(2026, 10, 29, 6, 30, tzinfo=UTC))
    monkeypatch.setattr(md, "datetime", Clock)
    monkeypatch.setattr(md, "monotonic", lambda: Clock.ticks)
    monkeypatch.setattr(trading_routes, "datetime", Clock)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as upstream:
        thread = Thread(target=upstream.serve_forever, daemon=True)
        thread.start()

        class LocalClient(MarketDataClient):
            @override
            def _get(self, url: str) -> bytes:
                route = "/history" if "/kline/kline?" in url else "/intraday"
                return super()._get(f"http://127.0.0.1:{upstream.server_port}{route}")

        try:
            provider = LocalClient()
            engine = Engine(Store(tmp_path / "http.sqlite3"), provider, clock=Clock.now)
            warm = provider.history("600519.SS")[0]
            monkeypatch.setattr(Clock, "current", datetime(2026, 10, 29, 7, 10, tzinfo=UTC))
            monkeypatch.setattr(Clock, "ticks", 3400)
            payload = request().model_copy(update={"limit_price": Decimal(112)}).model_dump(mode="json")
            with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
                assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
                outputs: list[str] = []
                for route in ("/api/orders/preview", "/api/orders/size", "/api/orders"):
                    response = http.post(url + route, json=payload)
                    outputs.append(f"{route}\nHTTP {response.status_code}\n{response.text}")
                    assert response.status_code == 200, response.text
                order = OrderView.model_validate_json(response.content)
                assert str(order.daily_upper) == "115.50"
                assert str(order.daily_lower) == "94.50"
                before = engine.store.read().model_dump_json()
                response = http.get(url + "/api/orders")
                assert response.status_code == 200
                assert response.json()[0]["first_candidate_interval"]["status"] == "available"
                assert engine.store.read().model_dump_json() == before
                assert "first_candidate_interval" not in before
                _ = (tmp_path / "http-responses.txt").write_text("\n".join(outputs))
            fresh = provider.history("600519.SS")[0]
            assert warm.close == 100
            assert fresh.close == 105
            assert fresh.retrieved_at > warm.retrieved_at
            assert len(history_calls) == 2
            _ = (tmp_path / "history-network.json").write_text(json.dumps(history_calls))
        finally:
            upstream.shutdown()
            thread.join(5)
            assert not thread.is_alive()


def test_snapshot_api_and_csv_recovery_same_values(engine: Engine, tmp_path: Path) -> None:
    held(engine)
    engine.provider = UnavailableProvider()
    capture(engine, NOW)
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=1))
    capture(engine, NOW + timedelta(minutes=1))
    point = snapshots(engine.store, engine.store.read().id)[0]
    before = engine.store.read()
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        response = http.get(url + "/api/snapshots")
        assert response.status_code == 200
        assert response.json() == [point.model_dump(mode="json")]
        query = f"?range=custom&start_date={NOW.date()}&end_date={NOW.date()}"
        exported = http.get(url + "/api/snapshots/export.csv" + query)
        assert exported.status_code == 200
        rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert len(rows[0]) == len(rows[1]) == 11
        assert rows[1][2] == point.captured_at.isoformat()
        assert rows[1][3] == str(point.total_assets_cny)
        assert rows[1][8] == NOW.isoformat()
        assert rows[1][9] == "missing"
        assert point.first_failure is not None
        assert rows[1][10] == "；".join(point.first_failure.issues)
        _ = (tmp_path / "snapshot-api.json").write_bytes(response.content)
        _ = (tmp_path / "snapshots.csv").write_bytes(exported.content)
    assert engine.store.read() == before
