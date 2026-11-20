"""通过真实 HTTP 和临时 SQLite 核对委托回执，覆盖只读与晚到提交。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.api_models import ConvertInput
from app.engine import Engine
from app.main import create_app
from app.models import OrderView
from app.operations import convert
from app.storage import read_rows
from tests.round_support import BarrierProvider, earn_dollars, reset_http, set_clock
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request, sample


def test_receipt_requires_login_and_reads_latest_without_writing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_clock(monkeypatch)
    payload = request()
    order = engine.place(payload, NOW)
    route = f"/api/orders/receipts/{payload.request_id}"
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.get(url + route).status_code == 401
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        with engine.store.transaction() as connection:
            before = read_rows(connection, "SELECT data FROM rounds ORDER BY rowid")
            requests_before = read_rows(connection, "SELECT * FROM requests ORDER BY id")
            response = http.get(url + route)
            assert response.status_code == 200
            returned = OrderView.model_validate_json(response.content)
            assert returned.id == order.id
            assert returned.status == "accepted"
            assert returned.waiting is not None
            assert read_rows(connection, "SELECT data FROM rounds ORDER BY rowid") == before
            assert read_rows(connection, "SELECT * FROM requests ORDER BY id") == requests_before
        engine.process(order.symbol, (sample(engine, order),), NOW + timedelta(minutes=6))
        engine.provider = ControlledProvider(fx_available=False)
        final = OrderView.model_validate_json(http.get(url + route).content)
        assert final.status == "filled"
        assert final.fill_price == Decimal(99)
        assert final.waiting is None
        assert final.id == order.id


def test_receipt_excludes_unknown_exchange_reset_and_order_ids(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    now = NOW.replace(hour=15)
    set_clock(monkeypatch, now)
    engine.provider = ControlledProvider(quote_time=now - timedelta(minutes=1))
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        earn_dollars(engine, http, url, now)
        exchange = ConvertInput(request_id=str(uuid4()), currency="USD", amount=Decimal(1))
        convert(engine, exchange, now + timedelta(minutes=10))
        order_id = engine.store.read().orders[0].id
        reset_http(http, url)
        with engine.store.transaction() as connection:
            requests_before = read_rows(connection, "SELECT * FROM requests ORDER BY id")
        conflict = http.post(
            url + "/api/orders",
            json=request().model_copy(update={"request_id": exchange.request_id}).model_dump(mode="json"),
            headers={"X-StockMock-Retry": "1"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
        with engine.store.transaction() as connection:
            assert read_rows(connection, "SELECT * FROM requests ORDER BY id") == requests_before
            reset_ids = read_rows(connection, "SELECT id FROM requests WHERE payload LIKE 'reset:%'")
            assert len(reset_ids) == 1
            for identifier in (str(uuid4()), exchange.request_id, reset_ids[0][0], order_id):
                response = http.get(url + f"/api/orders/receipts/{identifier}")
                assert response.status_code == 404, response.text
                assert '"order_receipt_missing"' in response.text


def test_receipt_missing_during_admission_becomes_found_after_late_commit(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_clock(monkeypatch)
    provider = BarrierProvider()
    engine.provider = provider
    payload = request()
    before = engine.store.read()
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as writer, client() as reader:
        for http in (writer, reader):
            _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(writer.post, url + "/api/orders", json=payload.model_dump(mode="json"))
            try:
                assert provider.entered.wait(timeout=5)
                response = reader.get(url + f"/api/orders/receipts/{payload.request_id}")
                assert response.status_code == 404
                assert engine.store.read() == before
            finally:
                provider.release.set()
            submitted = pending.result(timeout=5)
        assert submitted.status_code == 200
        receipt = reader.get(url + f"/api/orders/receipts/{payload.request_id}")
        assert receipt.status_code == 200
        assert OrderView.model_validate_json(receipt.content).id == OrderView.model_validate_json(submitted.content).id
        assert len(engine.store.read().orders) == 1
