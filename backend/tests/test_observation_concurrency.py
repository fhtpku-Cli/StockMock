"""两个独立 Store/Engine 的真实写事务竞争与在途下单例外。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest

from app.engine import Engine
from app.storage import Store
from tests.round_support import BarrierProvider
from tests.test_engine import NOW, request, sample


@pytest.mark.parametrize("first_price", ["101", "99"])
def test_two_clients_competing_versions_share_committed_outcome(engine: Engine, first_price: str) -> None:
    orders = [engine.place(request(), NOW) for _ in range(3)]
    other = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    barrier = Barrier(2)
    first = sample(engine, orders[0], first_price, 5)
    second = replace(first, price=Decimal(98), retrieved_at=NOW + timedelta(minutes=6))

    def process(client: Engine, *, revised: bool) -> None:
        _ = barrier.wait(timeout=5)
        client.process(orders[0].symbol, (second if revised else first,), NOW + timedelta(minutes=7))

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(process, engine, revised=False)
        two = pool.submit(process, other, revised=True)
        one.result(timeout=5)
        two.result(timeout=5)
    state = Store(engine.store.path).read()
    assert len({(o.status, o.fill_price, o.last_sample_at) for o in state.orders}) == 1
    assert all(o.last_sample_at == first.market_time for o in state.orders)
    if state.orders[0].status == "filled":
        price = state.orders[0].fill_price
        assert price in {Decimal(first_price), Decimal(98)}
        assert all(o.fill_observation is not None and o.fill_observation.price == price for o in state.orders)
        assert len(state.ledger) == 6
        assert len(state.lots) == 3
    else:
        assert first_price == "101"
        assert not state.ledger
        assert not state.lots
        assert state.cash["CNY"] == 1000000
    other.process(orders[0].symbol, (replace(first, price=Decimal(97)),), NOW + timedelta(minutes=8))
    assert engine.store.read() == state


def test_slow_place_may_use_later_version(engine: Engine) -> None:
    early = engine.place(request(), NOW)
    provider = BarrierProvider()
    delayed = Engine(Store(engine.store.path), provider, engine.catalog)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(delayed.place, request(), NOW)
        try:
            assert provider.entered.wait(timeout=5)
            engine.process(early.symbol, (sample(engine, early, "101", 5),), NOW + timedelta(minutes=6))
            assert len(engine.store.read().orders) == 1
        finally:
            provider.release.set()
        late = pending.result(timeout=5)
    assert late.active_at == early.active_at == NOW
    engine.process(early.symbol, (sample(engine, late, "99", 5),), NOW + timedelta(minutes=7))
    state = engine.store.read()
    assert [o.status for o in state.orders] == ["accepted", "filled"]
    assert state.orders[0].fill_observation is None
    assert state.orders[1].fill_observation is not None
    assert state.orders[1].fill_observation.price == 99
    assert {entry.order_id for entry in state.ledger} == {late.id}
