"""后台实际观察的时间边界、幂等和独立线程验证。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from threading import Event
from typing import override

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.plan_worker as plan_worker_module
import app.worker as worker_module
from app.activated_registry import RuleProvider
from app.engine import Engine
from app.instrument_rules import InstrumentRule
from app.main import configured_engine, create_app
from app.marketdata import MarketDataClient, MarketDataError, Quote
from app.plan_alerts import alerts, due_alert, observe, price_alerts, read_alert
from app.plan_worker import BATCH_SIZE, PlanWorker
from app.plans import create_plan, get_plan, update_plan
from app.storage import Store
from app.worker import Worker
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request
from tests.test_plans import body, edit

BASE = ControlledProvider().quote("600519.SS")


@pytest.mark.parametrize(
    "quote",
    [
        None,
        replace(BASE, market_time=NOW + timedelta(minutes=1)),
        replace(BASE, market_time=NOW - timedelta(minutes=21)),
        replace(BASE, tradable=False),
        replace(BASE, volume=Decimal(0)),
        replace(BASE, interval_start=None),
        replace(BASE, interval_start=BASE.market_time),
        replace(BASE, interval_start=BASE.market_time - timedelta(minutes=6)),
        replace(BASE, retrieved_at=NOW + timedelta(seconds=1)),
        replace(BASE, retrieved_at=BASE.market_time - timedelta(seconds=1)),
        replace(BASE, currency="USD"),
        replace(BASE, symbol="AAPL"),
        replace(BASE, price=Decimal("NaN")),
        replace(BASE, source=""),
        replace(BASE, market_time=BASE.market_time.replace(tzinfo=None)),
    ],
)
def test_invalid_observations_never_trigger_price(engine: Engine, quote: Quote | None) -> None:
    plan = create_plan(engine, body().model_copy(update={"target_price": Decimal(1)}), NOW)
    observed = observe(plan, quote, NOW)
    assert observed.status == "unavailable"
    price_alerts(engine.store, plan, observed)
    assert alerts(engine.store, plan.round_id) == []
    assert get_plan(engine.store, plan.id).monitoring.status == "unavailable"


def test_boundary_prices_each_revision_once_and_beijing_midnight(engine: Engine) -> None:
    plan = create_plan(
        engine, body().model_copy(update={"target_price": Decimal(100), "stop_price": Decimal(100)}), NOW
    )
    before = engine.store.read()
    for _ in range(2):
        price_alerts(engine.store, plan, observe(plan, BASE, NOW))
    assert {a.condition for a in alerts(engine.store, plan.round_id)} == {"target", "stop"}
    midnight = datetime(2026, 10, 21, 16, tzinfo=UTC)
    due_alert(engine.store, plan, midnight - timedelta(microseconds=1))
    assert len(alerts(engine.store, plan.round_id)) == 2
    due_alert(engine.store, plan, midnight)
    due_alert(Store(engine.store.path), plan, midnight)
    assert len(alerts(engine.store, plan.round_id)) == 3
    alert = alerts(engine.store, plan.round_id)[0]
    assert read_alert(engine.store, alert.id, NOW) == read_alert(engine.store, alert.id, NOW + timedelta(hours=1))
    changed = update_plan(engine, plan.id, edit(plan))
    price_alerts(engine.store, plan, observe(plan, BASE, NOW))
    assert len(alerts(engine.store, plan.round_id)) == 3
    price_alerts(engine.store, changed, observe(changed, BASE, NOW))
    assert len(alerts(engine.store, plan.round_id)) == 5
    assert engine.store.read() == before


class Clock:
    @staticmethod
    def now(_tz: tzinfo | None = None) -> datetime:
        return NOW + timedelta(minutes=6)


@dataclass(frozen=True, slots=True)
class CountedProvider(ControlledProvider):
    seen: list[str] | None = None
    failed_symbol: str = "600519.SS"

    @override
    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        if self.seen is not None:
            self.seen.append(symbol)
        if symbol == self.failed_symbol:
            raise MarketDataError("controlled_failure", "一个来源失败")
        return (replace(self.quote(symbol), retrieved_at=Clock.now()),)


def test_worker_only_active_is_bounded_and_isolates_source_failure(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_worker_module, "datetime", Clock)
    _ = create_plan(engine, body(), NOW)
    for item in engine.catalog[1 : BATCH_SIZE + 4]:
        _ = create_plan(engine, body().model_copy(update={"symbol": item.symbol}), NOW)
    _ = create_plan(engine, body().model_copy(update={"status": "draft", "symbol": "AAPL"}), NOW)
    seen: list[str] = []
    engine.plan_provider = CountedProvider(quote_time=NOW + timedelta(minutes=5), seen=seen)
    worker = PlanWorker(engine)
    worker.tick()
    assert len(seen) == BATCH_SIZE
    worker.tick()
    assert "600519.SS" in seen
    assert len(set(seen)) > BATCH_SIZE
    assert alerts(engine.store, engine.store.read().id)


def test_lifespan_alerts_without_browser_and_restart_no_duplicates(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = create_plan(engine, body().model_copy(update={"target_price": Decimal(99)}), NOW)
    engine.plan_provider = CountedProvider(quote_time=NOW + timedelta(minutes=5), failed_symbol="")
    event = Event()
    original = PlanWorker.tick

    def observed_tick(worker: PlanWorker) -> None:
        original(worker)
        event.set()

    monkeypatch.setattr(plan_worker_module, "datetime", Clock)
    monkeypatch.setattr(PlanWorker, "tick", observed_tick)
    for _ in range(2):
        event.clear()
        with TestClient(create_app(engine, PASSWORD)):
            assert event.wait(timeout=3)
    items = alerts(Store(engine.store.path), plan.round_id)
    assert {item.condition for item in items} == {"review_due", "target"}
    assert len(items) == 2
    assert engine.store.read().orders == []


def test_valid_time_but_non_session_sample_cannot_trigger(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = create_plan(engine, body().model_copy(update={"target_price": Decimal(1)}), NOW)
    noon = NOW.replace(hour=4)

    class NoonClock:
        @staticmethod
        def now(_tz: tzinfo | None = None) -> datetime:
            return noon

    monkeypatch.setattr(plan_worker_module, "datetime", NoonClock)
    engine.plan_provider = ControlledProvider(quote_time=noon - timedelta(minutes=1))
    PlanWorker(engine).tick()
    assert all(alert.condition == "review_due" for alert in alerts(engine.store, engine.store.read().id))


def test_slow_plan_client_lock_does_not_block_real_worker(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = create_plan(engine, body(), NOW)
    order = engine.place(request(), NOW)
    monkeypatch.setenv("PAPER_DB", str(engine.store.path))

    class ProbeClient(MarketDataClient):
        def load_probe(self) -> tuple[bytes, datetime]:
            return self._load("controlled:slow-plan", 60)

    monkeypatch.setattr(main_module, "MarketDataClient", ProbeClient)
    production = configured_engine()
    assert isinstance(production.plan_provider, RuleProvider)
    plan_client = production.plan_provider.client
    assert isinstance(production.provider, RuleProvider)
    assert plan_client is not production.provider.client
    entered, release, matched = Event(), Event(), Event()

    def slow_get(_client: MarketDataClient, _url: str) -> bytes:
        entered.set()
        assert release.wait(timeout=5)
        return b"controlled"

    def intraday(client: ProbeClient, _rule: InstrumentRule) -> tuple[Quote, ...]:
        if client is plan_client:
            _ = client.load_probe()
        return ControlledProvider(quote_time=NOW + timedelta(minutes=5), price=Decimal(99)).intraday(order.symbol)

    monkeypatch.setattr(MarketDataClient, "_get", slow_get)
    monkeypatch.setattr(MarketDataClient, "intraday", intraday)

    def quote(_self: MarketDataClient, _rule: InstrumentRule) -> Quote:
        return ControlledProvider(quote_time=NOW).quote(order.symbol)

    monkeypatch.setattr(MarketDataClient, "quote", quote)
    monkeypatch.setattr(plan_worker_module, "datetime", Clock)
    monkeypatch.setattr(worker_module, "datetime", Clock)

    def match_order() -> None:
        Worker(production).tick()
        matched.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(PlanWorker(production).tick)
        try:
            assert entered.wait(timeout=2)
            trading = pool.submit(match_order)
            assert matched.wait(timeout=2), "慢计划请求不应阻塞撮合"
            assert Store(engine.store.path).read().orders[0].status == "filled"
            trading.result()
        finally:
            release.set()
        future.result()
