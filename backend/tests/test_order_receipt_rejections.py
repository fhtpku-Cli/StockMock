"""恢复重试的最终拒绝必须阻断已经在途的原委托。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import override

import pytest

from app.engine import Engine
from app.main import create_app
from app.marketdata import MarketDataError, Quote
from app.models import OrderView
from app.storage import read_rows
from tests.round_support import BarrierProvider, set_clock
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request, sample


class RejectingRetryProvider(BarrierProvider):
    """原请求取得有效行情前暂停，恢复重试遇到空行情。"""

    @override
    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        if not self.entered.is_set():
            self.wait_once()
            return self.base.intraday(symbol)
        return ()


def test_retry_rejection_prevents_late_original_admission(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    set_clock(monkeypatch)
    provider = RejectingRetryProvider()
    engine.provider = provider
    payload = request().model_dump(mode="json")
    before = engine.store.read()
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as writer, client() as reader:
        for http in (writer, reader):
            assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(writer.post, url + "/api/orders", json=payload)
            try:
                assert provider.entered.wait(timeout=5)
                rejected = reader.post(url + "/api/orders", json=payload, headers={"X-StockMock-Retry": "1"})
            finally:
                provider.release.set()
            original = pending.result(timeout=5)
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "order_receipt_rejected"
        assert original.status_code == 409
        assert original.json() == rejected.json()
        engine.provider = ControlledProvider()
        replayed = writer.post(url + "/api/orders", json=payload)
        assert replayed.status_code == 409
        assert replayed.json() == rejected.json()
        receipt = reader.get(url + f"/api/orders/receipts/{payload['request_id']}")
        assert receipt.status_code == 409
        assert receipt.json() == rejected.json()
        conflict = reader.post(
            url + "/api/orders", json={**payload, "quantity": 200}, headers={"X-StockMock-Retry": "1"}
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert engine.store.read() == before
    with engine.store.transaction() as connection:
        assert len(read_rows(connection, "SELECT * FROM requests")) == 1
    restarted = Engine(engine.store, ControlledProvider(), engine.catalog)
    with server(create_app(restarted, PASSWORD, run_worker=False)) as url:
        with client() as http:
            assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
            persisted = http.get(url + f"/api/orders/receipts/{payload['request_id']}")
        assert persisted.status_code == 409
        assert persisted.json() == rejected.json()


def test_first_rejection_keeps_ordinary_post_retry_compatible(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    set_clock(monkeypatch)
    provider = RejectingRetryProvider()
    provider.entered.set()
    engine.provider = provider
    payload = request().model_dump(mode="json")
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        failed = http.post(url + "/api/orders", json=payload)
        assert failed.status_code == 409
        assert failed.json()["detail"]["code"] == "quote_unavailable"
        assert http.get(url + f"/api/orders/receipts/{payload['request_id']}").status_code == 404
        engine.provider = ControlledProvider()
        accepted = http.post(url + "/api/orders", json=payload)
        assert accepted.status_code == 200
        assert OrderView.model_validate_json(accepted.content).status == "accepted"


class DelayedRejectionProvider(BarrierProvider):
    """重试停在行情读取，另一请求先成功受理。"""

    @override
    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        if not self.entered.is_set():
            self.wait_once()
            return ()
        return self.base.intraday(symbol)


def test_accepted_original_wins_over_delayed_retry_rejection(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    set_clock(monkeypatch)
    provider = DelayedRejectionProvider()
    engine.provider = provider
    payload = request().model_dump(mode="json")
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as writer, client() as reader:
        for http in (writer, reader):
            assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(reader.post, url + "/api/orders", json=payload, headers={"X-StockMock-Retry": "1"})
            try:
                assert provider.entered.wait(timeout=5)
                accepted = writer.post(url + "/api/orders", json=payload)
                assert accepted.status_code == 200
                order = engine.store.read().orders[0]
                engine.process(order.symbol, (sample(engine, order),), NOW + timedelta(minutes=6))
            finally:
                provider.release.set()
            replayed = pending.result(timeout=5)
        assert replayed.status_code == 200
        returned = OrderView.model_validate_json(replayed.content)
        assert returned.id == OrderView.model_validate_json(accepted.content).id
        assert returned.status == "filled"
        assert len(engine.store.read().orders) == 1
        with engine.store.transaction() as connection:
            assert len(read_rows(connection, "SELECT * FROM requests")) == 1


class UnavailableProvider(ControlledProvider):
    @override
    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        raise MarketDataError("network_error", "测试供应商不可用")


def test_transport_failure_does_not_claim_final_rejection(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    set_clock(monkeypatch)
    engine.provider = UnavailableProvider()
    payload = request().model_dump(mode="json")
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        failed = http.post(url + "/api/orders", json=payload, headers={"X-StockMock-Retry": "1"})
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "network_error"
        assert http.get(url + f"/api/orders/receipts/{payload['request_id']}").status_code == 404
        with engine.store.transaction() as connection:
            assert read_rows(connection, "SELECT * FROM requests") == []


def test_retry_finalizes_validity_rejection(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    set_clock(monkeypatch)
    payload = request().model_copy(update={"expected_expires_at": NOW}).model_dump(mode="json")
    before = engine.store.read()
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        failed = http.post(url + "/api/orders", json=payload, headers={"X-StockMock-Retry": "1"})
        assert failed.status_code == 409
        assert failed.json()["detail"]["code"] == "order_receipt_rejected"
        assert "有效期已变化" in failed.json()["detail"]["message"]
        replayed = http.get(url + f"/api/orders/receipts/{payload['request_id']}")
        assert replayed.status_code == 409
        assert replayed.json() == failed.json()
        with engine.store.transaction() as connection:
            assert len(read_rows(connection, "SELECT * FROM requests")) == 1
    assert engine.store.read() == before
