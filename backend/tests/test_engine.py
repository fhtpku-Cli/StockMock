"""真实 SQLite 的受控事件时间集成场景，不连接生产行情。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.accounting import available, sellable
from app.api_models import ResetInput
from app.catalog import CATALOG, instrument
from app.engine import Engine
from app.marketdata import Bar, FxRate, MarketDataError, Quote
from app.models import DomainError, Order, OrderInput
from app.operations import reset
from app.storage import Store

NOW = datetime(2026, 10, 22, 2, 0, tzinfo=UTC)
PRIOR_DAY = (NOW - timedelta(days=1)).date()


@dataclass(frozen=True, slots=True)
class ControlledProvider:
    quote_time: datetime = NOW - timedelta(minutes=1)
    price: Decimal = Decimal(100)
    fx_available: bool = True
    history_day: date = PRIOR_DAY

    def quote(self, symbol: str) -> Quote:
        currency = instrument(symbol).currency
        return Quote(
            symbol,
            self.price,
            currency,
            self.quote_time,
            max(NOW, self.quote_time),
            "controlled-test",
            tradable=True,
            interval_start=self.quote_time - timedelta(minutes=5),
            volume=Decimal(1000),
        )

    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        return (self.quote(symbol),)

    def history(self, symbol: str) -> tuple[Bar, ...]:
        q = self.quote(symbol)
        return (
            Bar(
                symbol,
                self.history_day,
                self.price,
                self.price,
                self.price,
                self.price,
                Decimal(1000),
                q.currency,
                self.quote_time,
                NOW,
                "controlled-test",
            ),
        )

    def fx_rates(self) -> tuple[FxRate, ...]:
        if not self.fx_available:
            raise MarketDataError("test_outage", "受控失败")
        return (
            FxRate("USD", Decimal(7), NOW, NOW, "controlled-test"),
            FxRate("HKD", Decimal(".9"), NOW, NOW, "controlled-test"),
        )


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine(
        Store(tmp_path / "ledger.sqlite3"),
        ControlledProvider(),
        tuple(i.model_copy(update={"tradable": True}) for i in CATALOG),
    )


def request() -> OrderInput:
    return OrderInput(
        request_id=str(uuid4()),
        symbol="600519.SS",
        side="buy",
        order_type="limit",
        quantity=100,
        limit_price=Decimal(100),
    )


def sample(
    engine: Engine, order: Order, price: str = "99", offset: int = 5, *, interval_start: datetime | None = None
) -> Quote:
    q = engine.provider.quote(order.symbol)
    end = order.active_at + timedelta(minutes=offset)
    return replace(
        q,
        price=Decimal(price),
        market_time=end,
        retrieved_at=end,
        interval_start=interval_start if interval_start is not None else end - timedelta(minutes=5),
    )


def test_initial_cash_and_reservation_when_order_accepted(engine: Engine) -> None:
    # Given: 空账户；When: 提交委托；Then: 本金不变但可用资金扣除预留。
    order = engine.place(request(), NOW)
    assert order.status == "accepted"
    assert engine.store.read().cash["CNY"] == Decimal(1000000)
    assert available(engine.store.read(), "CNY") == Decimal("989995.00")
    assert not engine.store.read().ledger


def test_old_quote_does_not_fill(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    engine.process(order.symbol, engine.provider.intraday(order.symbol), NOW)
    assert engine.store.read().orders[0].status == "accepted"
    assert not engine.store.read().ledger


def test_complete_bar_cannot_overlap_submission(engine: Engine) -> None:
    order = engine.place(request(), NOW + timedelta(minutes=1))
    q = replace(sample(engine, order, offset=4), interval_start=NOW)
    engine.process(order.symbol, (q,), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "accepted"


def test_limit_fills_once_at_better_price(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    quotes = (sample(engine, order, "101", 5), sample(engine, order, "99", 10))
    engine.process(order.symbol, quotes, NOW + timedelta(minutes=11))
    engine.process(order.symbol, quotes, NOW + timedelta(minutes=12))
    state = engine.store.read()
    assert state.orders[0].fill_price == Decimal(99)
    assert state.cash["CNY"] == Decimal("990095.05")
    assert len(state.ledger) == 2
    assert sellable(state, order.symbol, NOW + timedelta(hours=1)) == 0
    assert sellable(state, order.symbol, NOW + timedelta(days=1)) == 100


def test_cancel_can_lose_to_earlier_market_event(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=6))
    assert available(engine.store.read(), "CNY") == Decimal("989995.00")
    engine.process(order.symbol, (sample(engine, order, "99", 5),), NOW + timedelta(minutes=7))
    assert engine.store.read().orders[0].status == "filled"


def test_cancel_cutoff_equal_complete_sample_fills(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=5))
    engine.process(order.symbol, (sample(engine, order, "99", 5),), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "filled"
    assert available(engine.store.read(), "CNY") == Decimal("990095.05")
    assert len(engine.store.read().ledger) == 2
    assert "撤单未生效" in engine.store.read().orders[0].status_reason


def test_idempotent_request_and_conflicting_reuse(engine: Engine) -> None:
    body = request()
    order = engine.place(body, NOW)
    assert engine.place(body, NOW).id == order.id
    with pytest.raises(DomainError, match="相同请求编号"):
        _ = engine.place(body.model_copy(update={"quantity": 200}), NOW)
    assert len(engine.store.read().orders) == 1


def test_parallel_orders_cannot_overspend(engine: Engine) -> None:

    body = request().model_copy(update={"quantity": 6000})

    def place(_index: int) -> str:
        try:
            return engine.place(body.model_copy(update={"request_id": str(uuid4())}), NOW).status
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(place, range(2)))
    assert sorted(statuses) == ["accepted", "insufficient_funds"]
    assert available(engine.store.read(), "CNY") >= 0


def test_stale_quote_rejected(engine: Engine) -> None:
    engine.provider = ControlledProvider(quote_time=NOW - timedelta(hours=1))
    with pytest.raises(DomainError) as error:
        _ = engine.place(request(), NOW)
    assert error.value.code == "quote_stale"


def test_market_protection_terminates(engine: Engine) -> None:
    body = request().model_copy(update={"order_type": "market", "limit_price": None})
    order = engine.place(body, NOW)
    engine.process(order.symbol, (sample(engine, order, "103"),), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "terminated"
    assert not engine.store.read().ledger


def test_restart_persists_reservations_then_expires(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    restarted = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    assert available(restarted.store.read(), "CNY") == Decimal("989995.00")
    restarted.process(order.symbol, (), order.expires_at + timedelta(minutes=21))
    assert restarted.store.read().orders[0].status == "terminated"
    assert available(restarted.store.read(), "CNY") == Decimal(1000000)


def test_round_reset_archives_and_old_request_does_not_duplicate(engine: Engine) -> None:
    body = request()
    order = engine.place(body, NOW)
    _ = engine.cancel(order.id, NOW)
    result = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    assert engine.place(body, NOW).id == order.id
    assert engine.store.read().id == result.round_id
    assert engine.store.read().orders == []


def test_daily_limit_edge_does_not_fill(engine: Engine) -> None:
    order = engine.place(request().model_copy(update={"limit_price": Decimal(110)}), NOW)
    engine.process(order.symbol, (sample(engine, order, "110"),), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "accepted"


def test_closed_auction_rejects_market(engine: Engine) -> None:
    now = datetime(2026, 10, 22, 6, 58, tzinfo=UTC)
    with pytest.raises(DomainError) as error:
        _ = engine.place(request().model_copy(update={"order_type": "market"}), now)
    assert error.value.code == "market_closed"


def test_future_sample_does_not_advance_or_fill(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    engine.process(order.symbol, (sample(engine, order, offset=10),), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "accepted"
    assert engine.store.read().orders[0].last_sample_at is None
    assert engine.store.read().ledger == []


@pytest.mark.parametrize("volume", [None, Decimal(0)])
def test_interval_without_positive_volume_never_fills(engine: Engine, volume: Decimal | None) -> None:
    order = engine.place(request(), NOW)
    q = replace(sample(engine, order), volume=volume)
    engine.process(order.symbol, (q,), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "accepted"
    assert engine.store.read().ledger == []


def test_interval_missing_start_never_fills(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    q = replace(sample(engine, order), interval_start=None)
    engine.process(order.symbol, (q,), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "accepted"


def test_future_reference_rejects_new_order(engine: Engine) -> None:
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=1))
    with pytest.raises(DomainError) as error:
        _ = engine.place(request(), NOW)
    assert error.value.code == "quote_unverified"


def test_expiration_waits_for_delayed_preclose_bar(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    event = order.expires_at - timedelta(minutes=2)
    q = replace(
        sample(engine, order), market_time=event, retrieved_at=event, interval_start=event - timedelta(minutes=5)
    )
    engine.process(order.symbol, (q,), order.expires_at + timedelta(minutes=2))
    assert engine.store.read().orders[0].status == "filled"
    assert engine.store.read().orders[0].filled_at == event


def test_expiration_watermark_releases_without_trade(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    event = order.expires_at + timedelta(minutes=3)
    q = replace(
        sample(engine, order), market_time=event, retrieved_at=event, interval_start=event - timedelta(minutes=5)
    )
    engine.process(order.symbol, (q,), order.expires_at + timedelta(minutes=4))
    assert engine.store.read().orders[0].status == "expired"
    assert available(engine.store.read(), "CNY") == Decimal(1000000)


def test_a_share_same_day_sell_rejected(engine: Engine) -> None:
    buy = engine.place(request(), NOW)
    engine.process(buy.symbol, (sample(engine, buy),), NOW + timedelta(minutes=6))
    body = request().model_copy(update={"side": "sell"})
    with pytest.raises(DomainError) as error:
        _ = engine.place(body, NOW + timedelta(minutes=3))
    assert error.value.code == "insufficient_shares"


def test_closed_round_cannot_be_restarted_with_pending_orders(engine: Engine) -> None:
    _ = engine.place(request(), NOW)
    with pytest.raises(DomainError) as error:
        _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    assert error.value.code == "open_orders"


def test_manual_symbol_pause_stops_fills_and_new_orders(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    engine.catalog = tuple(item.model_copy(update={"tradable": False}) for item in engine.catalog)
    engine.process(order.symbol, (sample(engine, order),), NOW + timedelta(minutes=6))
    assert engine.store.read().orders[0].status == "accepted"
    assert engine.store.read().ledger == []
    with pytest.raises(DomainError) as error:
        _ = engine.place(request(), NOW)
    assert error.value.code == "observation_only"
