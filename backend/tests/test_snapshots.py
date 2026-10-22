"""离线缺口、行情故障、并发观察与旧数据库升级的持久行为。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import override

import pytest

import app.snapshots as snapshot_module
from app.catalog import instrument
from app.engine import Engine
from app.marketdata import MarketDataError, Quote
from app.models import State
from app.snapshots import capture, snapshots
from app.storage import Store
from app.valuation import Valuation, stale_price, value_account
from app.worker import Worker
from tests.test_engine import NOW, ControlledProvider
from tests.test_personal import held


class UnavailableProvider(ControlledProvider):
    @override
    def quote(self, symbol: str) -> Quote:
        raise MarketDataError("test_outage", symbol)


def test_missing_snapshot_persists_and_worker_survives(engine: Engine) -> None:
    held(engine)
    engine.provider = UnavailableProvider()
    worker = Worker(engine)
    worker.tick()
    worker.tick()
    points = snapshots(Store(engine.store.path), engine.store.read().id)
    assert len(points) == 1
    assert points[0].status == "missing"
    assert points[0].total_assets_cny is None
    assert worker.view.last_run is not None


def test_simultaneous_snapshot_collectors_only_record_one(engine: Engine) -> None:
    def collect(_index: int) -> None:
        capture(engine, NOW)

    with ThreadPoolExecutor(max_workers=4) as pool:
        _ = list(pool.map(collect, range(4)))
    assert len(snapshots(Store(engine.store.path), engine.store.read().id)) == 1


def test_balance_change_during_collection_discards_mixed_snapshot(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = value_account

    def changing_value(current: Engine, state: State, now: datetime) -> Valuation:
        result = original(current, state, now)
        with current.store.transaction() as connection:
            latest = current.store.load(connection)
            latest.cash["CNY"] += 1
            current.store.save(connection, latest)
        return result

    monkeypatch.setattr(snapshot_module, "value_account", changing_value)
    capture(engine, NOW)
    assert snapshots(engine.store, engine.store.read().id) == []
    monkeypatch.setattr(snapshot_module, "value_account", original)
    capture(engine, NOW + timedelta(minutes=1))
    assert snapshots(engine.store, engine.store.read().id)[0].total_assets_cny == Decimal(1000001)


def test_old_database_adds_tables_without_rewriting_round(engine: Engine) -> None:
    held(engine)
    state = engine.store.read()
    with engine.store.transaction() as connection:
        _ = connection.execute("DROP TABLE snapshots")
        _ = connection.execute("DROP TABLE adjustments")
    restored = Store(engine.store.path)
    assert restored.read() == state
    assert snapshots(restored, state.id) == []


def test_stale_fx_prevents_total_even_when_positions_quote_is_current(engine: Engine) -> None:
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(100)
        engine.store.save(connection, state)
    value = value_account(engine, engine.store.read(), NOW + timedelta(days=6))
    assert value.total is None
    assert any("汇率" in issue for issue in value.issues)


def test_worker_recovers_from_transient_database_read_error(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Store.read

    def unavailable(_store: Store) -> State:
        raise sqlite3.OperationalError

    worker = Worker(engine)
    monkeypatch.setattr(Store, "read", unavailable)
    worker.tick()
    assert worker.view.status == "账户暂不可读，下轮重试"
    monkeypatch.setattr(Store, "read", original)
    worker.tick()
    assert worker.view.status == "running"


def test_closed_session_requires_price_near_close_not_just_same_day(engine: Engine) -> None:
    item = instrument("600519.SS", engine.catalog)
    assert stale_price(item, NOW, datetime(2026, 10, 22, 8, 0, tzinfo=UTC))
    friday_close = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)
    assert not stale_price(item, friday_close, friday_close + timedelta(days=1))
    lunch = datetime(2026, 10, 22, 4, 30, tzinfo=UTC)
    assert not stale_price(item, lunch - timedelta(hours=1), lunch)
    assert stale_price(item, NOW, lunch)
    assert stale_price(item, NOW, datetime(2026, 10, 22, 6, 58, tzinfo=UTC))
