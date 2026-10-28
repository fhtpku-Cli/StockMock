"""新观察规则：受控输入经过真实 SQLite，不访问正式账户。"""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import TypeAdapter

from app.engine import Engine
from app.marketdata import MarketDataError, Quote
from tests.test_engine import NOW, request, sample


def test_observation_saved_and_revision_ignored(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    quote = replace(sample(engine, order, "99.1234", 5), retrieved_at=NOW + timedelta(minutes=6))
    engine.process(order.symbol, (quote,), NOW + timedelta(minutes=6))
    before = engine.store.read()
    observation = before.orders[0].fill_observation
    assert observation is not None
    snapshot = TypeAdapter(dict[str, str | bool]).validate_json(observation.model_dump_json())
    assert snapshot == {
        "symbol": quote.symbol,
        "currency": quote.currency,
        "source": quote.source,
        "interval_start": "2026-10-22T02:00:00Z",
        "market_time": "2026-10-22T02:05:00Z",
        "retrieved_at": "2026-10-22T02:06:00Z",
        "price": "99.1234",
        "volume": "1000",
        "tradable": True,
    }
    engine.process(order.symbol, (replace(quote, price=Decimal(98)),), NOW + timedelta(minutes=7))
    assert engine.store.read() == before


def test_invalid_observation_does_not_consume_watermark(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    quote = replace(sample(engine, order, offset=5), retrieved_at=NOW + timedelta(minutes=6))
    engine.process(order.symbol, (replace(quote, volume=Decimal(0)),), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].last_sample_at is None
    engine.process(order.symbol, (quote,), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "filled"


def test_duplicate_batch_rejects_before_any_processing(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    quote = replace(sample(engine, order, offset=5), retrieved_at=NOW + timedelta(minutes=6))
    before = engine.store.read()
    with pytest.raises(MarketDataError, match="duplicate"):
        engine.process(order.symbol, (quote, replace(quote, price=Decimal(98))), NOW + timedelta(minutes=6))
    assert engine.store.read() == before


@pytest.mark.parametrize(
    "invalid", ["future", "naive", "zero", "nan", "infinite", "shape", "grid", "source", "currency", "symbol"]
)
def test_invalid_then_valid_same_label(engine: Engine, invalid: str) -> None:
    order = engine.place(request(), NOW)
    good = replace(sample(engine, order, offset=5), retrieved_at=NOW + timedelta(minutes=6))
    variants = {
        "future": replace(good, retrieved_at=NOW + timedelta(minutes=7)),
        "naive": replace(good, retrieved_at=good.retrieved_at.replace(tzinfo=None)),
        "zero": replace(good, price=Decimal(0)),
        "nan": replace(good, price=Decimal("NaN")),
        "infinite": replace(good, volume=Decimal("Infinity")),
        "shape": replace(good, interval_start=NOW + timedelta(minutes=1)),
        "grid": replace(good, market_time=NOW + timedelta(minutes=4), interval_start=NOW - timedelta(minutes=1)),
        "source": replace(good, source="wrong-source"),
        "currency": replace(good, currency="USD"),
        "symbol": replace(good, symbol="AAPL"),
    }
    before = engine.store.read()
    engine.process(order.symbol, (variants[invalid],), NOW + timedelta(minutes=6))
    assert engine.store.read() == before
    engine.process(order.symbol, (good,), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "filled"


def test_valid_nonfill_consumes_version_across_restart(engine: Engine) -> None:
    from app.storage import Store  # noqa: PLC0415

    order = engine.place(request(), NOW)
    first = sample(engine, order, "101", 5)
    engine.process(order.symbol, (first,), NOW + timedelta(minutes=6))
    before = engine.store.read()
    assert before.orders[0].last_sample_at == first.market_time
    restarted = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    restarted.process(order.symbol, (replace(first, price=Decimal(99)),), NOW + timedelta(minutes=7))
    assert restarted.store.read() == before


def test_save_failure_rolls_back_everything(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlite3 import Connection, OperationalError  # noqa: PLC0415

    from app.models import State  # noqa: PLC0415
    from app.storage import Store  # noqa: PLC0415

    order = engine.place(request(), NOW)
    before = engine.store.read()
    original = Store.save

    def fail_after_save(store: Store, connection: Connection, state: State) -> None:
        original(store, connection, state)
        raise OperationalError("受控保存后失败")

    with monkeypatch.context() as patch:
        patch.setattr(Store, "save", fail_after_save)
        with pytest.raises(OperationalError, match="受控保存后失败"):
            engine.process(order.symbol, (sample(engine, order, "99", 5),), NOW + timedelta(minutes=6))
    assert Store(engine.store.path).read() == before
    engine.process(order.symbol, (sample(engine, order, "98", 5),), NOW + timedelta(minutes=7))
    assert engine.store.read().orders[0].fill_price == 98


def test_snapshot_validation_failure_is_before_accounting(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import accounting  # noqa: PLC0415

    first = engine.place(request(), NOW)
    second = engine.place(request(), NOW)
    calls = 0

    def invalid_once(quote: Quote) -> dict[str, str | Decimal | datetime | bool | None]:
        nonlocal calls
        calls += 1
        # 只在首单快照构造边界制造实际 Pydantic 失败。
        if calls == 1:
            return {"symbol": quote.symbol}
        return {
            "symbol": quote.symbol,
            "currency": quote.currency,
            "source": quote.source,
            "price": quote.price,
            "volume": quote.volume,
            "tradable": quote.tradable,
            "interval_start": quote.interval_start,
            "market_time": quote.market_time,
            "retrieved_at": quote.retrieved_at,
        }

    monkeypatch.setattr(accounting, "asdict", invalid_once)
    engine.process(first.symbol, (sample(engine, first, offset=5),), NOW + timedelta(minutes=6))
    state = engine.store.read()
    assert [o.status for o in state.orders] == ["terminated", "filled"]
    assert state.orders[0].fill_observation is None
    assert state.orders[1].fill_observation is not None
    assert state.cash["CNY"] == Decimal("990095.05")
    assert {entry.order_id for entry in state.ledger} == {second.id}


@pytest.mark.parametrize("duplicate", [False, True])
def test_invalid_after_cutoff_cannot_confirm_early(engine: Engine, *, duplicate: bool) -> None:
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=5))
    quote = sample(engine, order, offset=10)
    before = engine.store.read()
    if duplicate:
        with pytest.raises(MarketDataError, match="duplicate"):
            engine.process(order.symbol, (quote, quote), NOW + timedelta(minutes=11))
    else:
        engine.process(order.symbol, (replace(quote, volume=Decimal(0)),), NOW + timedelta(minutes=11))
    assert engine.store.read() == before
