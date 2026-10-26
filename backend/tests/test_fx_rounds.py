"""通过真实下单、换汇 HTTP 和 SQLite 验证跨轮次竞争。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.api_models import ConvertInput
from app.engine import Engine
from app.main import create_app
from app.storage import read_rows
from tests.round_support import BarrierProvider, MarketClock, earn_dollars, reset_http, set_clock
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider


@pytest.mark.parametrize("fund_new_round", [False, True])
def test_conversion_waiting_for_fx_rejects_reset_even_when_new_round_has_dollars(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, *, fund_new_round: bool
) -> None:
    # Given: 旧轮通过正常交易持有美元，首次换汇请求在取汇率时等待。
    now = NOW.replace(hour=15)
    set_clock(monkeypatch, now)
    base = ControlledProvider(quote_time=now - timedelta(minutes=1))
    engine.provider = base
    payload = ConvertInput(request_id=str(uuid4()), currency="USD", amount=Decimal(100))
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        for http in (first, second):
            _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        earn_dollars(engine, second, url, now)
        old_id = engine.store.read().id
        MarketClock.current = now + timedelta(minutes=6)
        provider = BarrierProvider(base=base, pause_fx=True)
        engine.provider = provider
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.post, url + "/api/fx/convert", json=payload.model_dump(mode="json"))
            try:
                assert provider.entered.wait(timeout=5)
                MarketClock.current = now + timedelta(minutes=7)
                reset_http(second, url)
                if fund_new_round:
                    earn_dollars(engine, second, url, now + timedelta(minutes=8))
                old = engine.store.read_round(old_id)
                current = engine.store.read()
            finally:
                provider.release.set()
            # When: 原轮的换汇请求在重开后取得汇率。
            response = pending.result(timeout=5)
        # Then: 即使新轮有足够美元，也必须报轮次冲突且不记账。
        assert response.status_code == 409
        assert '"round_changed"' in response.text
        assert engine.store.read_round(old_id) == old
        assert engine.store.read() == current
        with engine.store.transaction() as connection:
            assert not read_rows(connection, "SELECT id FROM requests WHERE id=?", (payload.request_id,))


def test_overlapping_conversion_retry_wins_before_round_conflict(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 两个同 ID 请求重叠，第二个已经成功换汇并重开轮次。
    now = NOW.replace(hour=15)
    set_clock(monkeypatch, now)
    base = ControlledProvider(quote_time=now - timedelta(minutes=1))
    engine.provider = base
    payload = ConvertInput(request_id=str(uuid4()), currency="USD", amount=Decimal(100))
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        for http in (first, second):
            _ = http.post(url + "/api/auth/login", json={"password": PASSWORD})
        earn_dollars(engine, second, url, now)
        provider = BarrierProvider(base=base, pause_fx=True)
        engine.provider = provider
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.post, url + "/api/fx/convert", json=payload.model_dump(mode="json"))
            try:
                assert provider.entered.wait(timeout=5)
                completed = second.post(url + "/api/fx/convert", json=payload.model_dump(mode="json"))
                assert completed.status_code == 200
                old_id = engine.store.read().id
                reset_http(second, url)
                old = engine.store.read_round(old_id)
                current = engine.store.read()
            finally:
                provider.release.set()
            # When: 旧请求继续，随后再次从入口重试已成功请求。
            response = pending.result(timeout=5)
        assert response.status_code == 200
        assert second.post(url + "/api/fx/convert", json=payload.model_dump(mode="json")).status_code == 200
        # Then: 同 ID 成功结果优先于轮次检查，两轮现金和流水都不变化。
        assert engine.store.read_round(old_id) == old
        assert engine.store.read() == current
