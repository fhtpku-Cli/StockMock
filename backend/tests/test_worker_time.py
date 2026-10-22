from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from app import worker as worker_module
from app.engine import Engine
from app.marketdata import Quote
from app.worker import Worker
from tests.test_engine import NOW, ControlledProvider, request, sample


@pytest.mark.parametrize("cutoff", ["none", "cancel", "expiry"])
def test_worker_confirms_after_fetch_and_honors_event_cutoff(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, cutoff: str
) -> None:
    order = engine.place(request(), NOW)
    deadline = NOW + timedelta(minutes=4, seconds=30)
    if cutoff == "cancel":
        _ = engine.cancel(order.id, deadline)
    elif cutoff == "expiry":
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.orders[0] = order.model_copy(update={"expires_at": deadline})
            engine.store.save(connection, state)
    observed = NOW + timedelta(minutes=5, seconds=1)
    completed = NOW + timedelta(minutes=5, seconds=2)
    clock = [NOW + timedelta(minutes=4, seconds=59)]
    quote = replace(sample(engine, order, offset=5), retrieved_at=observed)

    class Clock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

    def delayed(_provider: ControlledProvider, _symbol: str) -> tuple[Quote, ...]:
        clock[0] = completed
        return (quote,)

    monkeypatch.setattr(worker_module, "datetime", Clock)
    monkeypatch.setattr(ControlledProvider, "intraday", delayed)
    worker = Worker(engine)
    worker.tick()
    state = engine.store.read()
    actual = state.orders[0]
    assert actual.confirmed_at is not None
    assert actual.confirmed_at == completed
    assert actual.confirmed_at >= quote.retrieved_at
    assert worker.view.last_run == completed
    if cutoff == "none":
        assert actual.status == "filled"
        assert actual.filled_at == quote.market_time
        assert len(state.ledger) == 2
    else:
        assert actual.status == ("cancelled" if cutoff == "cancel" else "expired")
        assert not state.ledger
        assert not state.lots
