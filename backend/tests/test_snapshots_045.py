"""同日恢复、首次提交竞争、午夜和旧JSON：受控来源与真实SQLite。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Event
from typing import override
from uuid import uuid4

import pytest

import app.snapshots as module
from app.adjustments import apply_action
from app.api_models import ResetInput
from app.engine import Engine
from app.marketdata import MarketDataError, Quote
from app.models import State
from app.operations import reset
from app.personal_models import ValuationSnapshot
from app.snapshots import capture, snapshots
from app.storage import Store, read_rows
from app.valuation import Valuation, value_account
from tests.test_engine import NOW, ControlledProvider, request
from tests.test_personal import action, held
from tests.test_snapshots import UnavailableProvider


@pytest.mark.parametrize("status", ["missing", "stale"])
def test_same_day_recovers_with_original_failure(engine: Engine, status: str) -> None:
    held(engine)
    engine.provider = (
        UnavailableProvider() if status == "missing" else ControlledProvider(quote_time=NOW - timedelta(hours=2))
    )
    capture(engine, NOW)
    initial = snapshots(engine.store, engine.store.read().id)[0]
    engine.provider = ControlledProvider(quote_time=NOW - timedelta(hours=2))
    capture(engine, NOW + timedelta(minutes=1))
    assert snapshots(engine.store, initial.round_id) == [initial]
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=2), price=Decimal(110))
    capture(engine, NOW + timedelta(minutes=2))
    complete = snapshots(Store(engine.store.path), initial.round_id)[0]
    assert complete.id == initial.id
    assert complete.status == "complete"
    assert complete.total_assets_cny == Decimal("1002000.00")
    assert complete.captured_at == NOW + timedelta(minutes=2)
    assert complete.price_times["600519.SS"] == NOW + timedelta(minutes=2)
    assert complete.issues == []
    assert complete.first_failure is not None
    assert complete.first_failure.captured_at == NOW
    assert complete.first_failure.status == status
    assert complete.first_failure.issues == initial.issues
    engine.provider = ControlledProvider(price=Decimal(120))
    capture(engine, NOW + timedelta(minutes=3))
    engine.provider = UnavailableProvider()
    capture(engine, NOW + timedelta(minutes=4))
    assert snapshots(engine.store, initial.round_id) == [complete]


@dataclass(frozen=True, slots=True)
class PausedProvider(ControlledProvider):
    entered: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    failing: bool = False

    @override
    def quote(self, symbol: str) -> Quote:
        self.entered.set()
        assert self.release.wait(5)
        if self.failing:
            raise MarketDataError("controlled_failure", "受控迟到失败")
        return super(PausedProvider, self).quote(symbol)


def test_first_complete_commit_wins_over_earlier_started_and_late_failure(engine: Engine) -> None:
    held(engine)
    engine.provider = UnavailableProvider()
    capture(engine, NOW)
    first = snapshots(engine.store, engine.store.read().id)[0]
    slow = PausedProvider(price=Decimal(150))
    failure = PausedProvider(failing=True)
    early_engine = Engine(Store(engine.store.path), slow)
    failure_engine = Engine(Store(engine.store.path), failure)
    with ThreadPoolExecutor(max_workers=2) as pool:
        early = pool.submit(capture, early_engine, NOW + timedelta(minutes=1))
        late = pool.submit(capture, failure_engine, NOW + timedelta(minutes=1))
        try:
            assert slow.entered.wait(5)
            assert failure.entered.wait(5)
            engine.provider = ControlledProvider(price=Decimal(110))
            capture(engine, NOW + timedelta(minutes=2))
            winner = snapshots(engine.store, first.round_id)[0]
            assert winner.total_assets_cny == Decimal("1002000.00")
        finally:
            slow.release.set()
            failure.release.set()
        early.result(5)
        late.result(5)
    assert snapshots(engine.store, first.round_id) == [winner]
    assert winner.id == first.id
    assert winner.first_failure == first.first_failure


@pytest.mark.parametrize("mutation", ["trade", "cash", "split", "dividend", "reset"])
def test_state_change_discards_observation_and_retries(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    held(engine)

    def change(current: Engine, state: State, now: datetime) -> Valuation:
        result = value_account(current, state, now)
        if mutation == "trade":
            _ = current.place(request(), now)
        elif mutation == "cash":
            with current.store.transaction() as connection:
                latest = current.store.load(connection)
                latest.cash["CNY"] += Decimal(1)
                current.store.save(connection, latest)
        elif mutation in {"split", "dividend"}:
            body = action(current)
            if mutation == "split":
                body = body.model_copy(
                    update={"kind": "split", "cash_per_share": None, "numerator": 2, "denominator": 1}
                )
            _ = apply_action(current, body, now)
        else:
            _ = reset(current, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), now)
        return result

    monkeypatch.setattr(module, "value_account", change)
    old_round = engine.store.read().id
    capture(engine, NOW)
    assert snapshots(engine.store, old_round) == []
    assert snapshots(engine.store, engine.store.read().id) == []
    monkeypatch.setattr(module, "value_account", value_account)
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=1))
    capture(engine, NOW + timedelta(minutes=1))
    assert len(snapshots(engine.store, engine.store.read().id)) == 1


def test_midnight_discards_then_new_day_capture(engine: Engine) -> None:
    before = NOW.replace(hour=15, minute=59, second=59)
    after = before + timedelta(seconds=2)
    times = iter((before, after))
    capture(engine, clock=lambda: next(times))
    assert snapshots(engine.store, engine.store.read().id) == []
    capture(engine, clock=lambda: after)
    point = snapshots(engine.store, engine.store.read().id)[0]
    assert point.day == after.date() + timedelta(days=1)
    assert point.captured_at == after


def test_old_json_upgrade_preserves_past_and_archived_rows(engine: Engine) -> None:
    held(engine)
    engine.provider = UnavailableProvider()
    capture(engine, NOW)
    state = engine.store.read()
    point = snapshots(engine.store, state.id)[0]
    old_json = point.model_dump_json(exclude={"first_failure"})
    with engine.store.transaction() as connection:
        _ = connection.execute("UPDATE snapshots SET data=?", (old_json,))
    assert ValuationSnapshot.model_validate_json(old_json).first_failure is None
    engine.provider = ControlledProvider()
    capture(engine, NOW + timedelta(minutes=1))
    recovered = snapshots(Store(engine.store.path), state.id)[0]
    assert recovered.first_failure is not None
    assert recovered.first_failure.captured_at == NOW
    with engine.store.transaction() as connection:
        original = read_rows(connection, "SELECT data FROM snapshots")
    capture(engine, NOW + timedelta(days=1))
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW + timedelta(days=1))
    capture(engine, NOW + timedelta(days=1))
    with engine.store.transaction() as connection:
        assert (
            read_rows(connection, "SELECT data FROM snapshots WHERE round_id=? AND day=?", (state.id, str(point.day)))
            == original
        )
    assert len(snapshots(engine.store, state.id)) == 2
    assert len(snapshots(engine.store, engine.store.read().id)) == 1


def test_past_and_archived_missing_rows_never_recover(engine: Engine) -> None:
    held(engine)
    engine.provider = UnavailableProvider()
    capture(engine, NOW)
    old_round = engine.store.read().id
    first = snapshots(engine.store, old_round)[0]
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(days=1))
    capture(engine, NOW + timedelta(days=1))
    assert snapshots(engine.store, old_round)[0] == first
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW + timedelta(days=1))
    capture(engine, NOW + timedelta(days=1))
    assert snapshots(engine.store, old_round)[0] == first


def test_completion_time_is_after_acquisition(engine: Engine) -> None:
    held(engine)
    times = iter((NOW, NOW + timedelta(seconds=3), NOW + timedelta(seconds=4)))
    capture(engine, clock=lambda: next(times))
    point = snapshots(engine.store, engine.store.read().id)[0]
    assert point.captured_at == NOW + timedelta(seconds=3)
    assert point.status == "complete"
    assert point.first_failure is None
