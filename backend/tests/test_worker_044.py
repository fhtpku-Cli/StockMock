"""0.4.4 Worker 唤醒、重启与连续故障的受控时钟实测。"""

from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from app import worker as worker_module
from app.accounting import available
from app.engine import Engine
from app.marketdata import MarketDataError, Quote
from app.worker import Worker
from tests.test_engine import NOW, ControlledProvider, request, sample


def test_worker_failure_clock_when_sleep_restart_and_recovery(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 截止已过、后台失败后停止超过五分钟，且时钟为明确 UTC。
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=10))
    clock = [NOW + timedelta(minutes=15)]
    outage = [True]

    class Clock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    def fetch(_provider: ControlledProvider, _symbol: str) -> tuple[Quote, ...]:
        if outage[0]:
            raise MarketDataError("test_outage", "受控失败")
        return (sample(engine, order, offset=5),)

    monkeypatch.setattr(worker_module, "datetime", Clock)
    monkeypatch.setattr(ControlledProvider, "intraday", fetch)
    worker = Worker(engine)
    worker.tick()
    # When: 三十分钟后唤醒首次失败，然后服务重启首次失败，下一轮成功。
    clock[0] += timedelta(minutes=30)
    worker.tick()
    assert engine.store.read().orders[0].status == "cancel_pending"
    worker = Worker(engine)
    worker.tick()
    assert engine.store.read().orders[0].status == "cancel_pending"
    clock[0] += timedelta(minutes=1)
    outage[0] = False
    worker.tick()
    # Then: 历史有效区间照常成交，失败计时清空。
    assert engine.store.read().orders[0].status == "filled"
    assert not worker.failed_since


def test_worker_when_continuous_outage_releases(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 已过截止且无最后候选区间，连续正常 tick 中行情请求失败。
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=10))
    clock = [NOW + timedelta(minutes=30)]

    class Clock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    def fetch(_provider: ControlledProvider, _symbol: str) -> tuple[Quote, ...]:
        raise MarketDataError("test_outage", "受控失败")

    monkeypatch.setattr(worker_module, "datetime", Clock)
    monkeypatch.setattr(ControlledProvider, "intraday", fetch)
    worker = Worker(engine)
    # When: 每分钟 tick，累计持续失败二十分钟。
    for minute in range(21):
        clock[0] = NOW + timedelta(minutes=30 + minute)
        worker.tick()
        if minute < 20:
            assert engine.store.read().orders[0].status == "cancel_pending"
    # Then: 精确满二十分钟允许终止，释放占用。
    state = engine.store.read()
    assert state.orders[0].status == "terminated"
    assert available(state, "CNY") == 1000000
    assert not state.ledger


def test_worker_restarts_failure_clock_when_symbol_returns(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 证券请求失败一次，随后该证券没有活动订单。
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=10))
    clock = [NOW + timedelta(minutes=31)]

    class Clock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    outage = [True]

    def fetch(_provider: ControlledProvider, _symbol: str) -> tuple[Quote, ...]:
        if not outage[0]:
            return (_provider.quote(_symbol),)
        raise MarketDataError("test_outage", "受控失败")

    monkeypatch.setattr(worker_module, "datetime", Clock)
    monkeypatch.setattr(ControlledProvider, "intraday", fetch)
    worker = Worker(engine)
    worker.tick()
    assert worker.failed_since[order.symbol] == clock[0]
    engine.process(order.symbol, (), clock[0])
    clock[0] += timedelta(minutes=1)
    worker.tick()
    assert not worker.failed_since
    engine.provider = ControlledProvider(quote_time=clock[0])
    outage[0] = False
    _ = engine.place(request(), clock[0])
    outage[0] = True
    # When: 无长 tick 间隔、同证券重新出现后的第一次请求失败。
    clock[0] += timedelta(minutes=1)
    worker.tick()
    # Then: 新失败起点是此次请求，未沿用旧记录，新单保持活动。
    assert worker.failed_since[order.symbol] == clock[0]
    assert engine.store.read().orders[-1].status == "accepted"


@pytest.mark.parametrize("recover", [False, True])
def test_duplicate_batch_uses_failure_protection(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, *, recover: bool
) -> None:
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=10))
    clock = [NOW + timedelta(minutes=30)]
    duplicate = [True]
    quote = sample(engine, order, offset=5)

    class Clock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    def fetch(_provider: ControlledProvider, _symbol: str) -> tuple[Quote, ...]:
        return (quote, quote) if duplicate[0] else (quote,)

    monkeypatch.setattr(worker_module, "datetime", Clock)
    monkeypatch.setattr(ControlledProvider, "intraday", fetch)
    worker = Worker(engine)
    worker.tick()
    assert worker.failed_since[order.symbol] == clock[0]
    assert engine.store.read().orders[0].status == "cancel_pending"
    assert engine.store.read().orders[0].last_sample_at is None
    if recover:
        duplicate[0] = False
        clock[0] += timedelta(minutes=1)
        worker.tick()
        assert engine.store.read().orders[0].status == "filled"
        assert not worker.failed_since
    else:
        for minute in range(1, 21):
            clock[0] = NOW + timedelta(minutes=30 + minute)
            worker.tick()
            assert engine.store.read().orders[0].status == ("terminated" if minute == 20 else "cancel_pending")
        assert not engine.store.read().ledger
        assert available(engine.store.read(), "CNY") == 1000000
