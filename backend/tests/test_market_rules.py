"""市场规则、数量计算与公司行动竞争使用受控区间验证。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.adjustments import apply_action
from app.calendar import is_open
from app.catalog import instrument
from app.engine import Engine
from app.marketdata import FxRate, Quote
from app.models import DomainError, Lot, OrderInput
from app.order_builder import price_bound
from app.order_rules import tick_size
from app.sizing import size_order
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_personal import action, held


def test_hk_dynamic_ticks_board_lot_and_hkd_fill(engine: Engine) -> None:
    item = instrument("0700.HK", engine.catalog)
    assert tick_size(item, Decimal(100)) != tick_size(item, Decimal(1))
    bad = request().model_copy(update={"symbol": item.symbol, "limit_price": Decimal("100.001")})
    with pytest.raises(DomainError, match="步长"):
        _ = engine.place(bad, NOW)
    order = engine.place(request().model_copy(update={"symbol": item.symbol}), NOW)
    engine.process(item.symbol, (sample(engine, order, "99", 5),), NOW + timedelta(minutes=6))
    state = engine.store.read()
    assert state.orders[0].status == "filled"
    assert state.lots[0].sellable_at == order.active_at + timedelta(minutes=5)
    assert {row.currency for row in state.ledger} == {"HKD", "CNY"}


def test_hk_odd_lot_split_rejected_without_changing_book(engine: Engine) -> None:
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="0700.HK", quantity=100, cost=Decimal(10000), sellable_at=NOW))
        engine.store.save(connection, state)
    before = engine.store.read()
    body = action(engine).model_copy(
        update={"symbol": "0700.HK", "kind": "split", "cash_per_share": None, "numerator": 3, "denominator": 2}
    )
    with pytest.raises(DomainError) as error:
        _ = apply_action(engine, body, NOW)
    assert error.value.code == "odd_lot_unsupported"
    assert engine.store.read() == before


def test_us_only_regular_session_and_subdollar_ticks(engine: Engine) -> None:
    item = instrument("AAPL", engine.catalog)
    assert not is_open(item, datetime(2026, 10, 22, 13, 29, tzinfo=UTC))
    assert is_open(item, datetime(2026, 10, 22, 13, 30, tzinfo=UTC))
    assert not is_open(item, datetime(2026, 10, 22, 20, 0, tzinfo=UTC))
    assert tick_size(item, Decimal(".9")) == Decimal(".0001")
    assert tick_size(item, Decimal("1.0")) == Decimal(".01")
    quote = engine.provider.quote(item.symbol)
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 1, "limit_price": Decimal(".9999")})
    assert price_bound(item, body, quote, NOW) == Decimal(".9999")


def test_market_close_lunch_and_overlong_interval_not_eligible(engine: Engine) -> None:
    item = instrument("600519.SS", engine.catalog)
    assert not is_open(item, datetime(2026, 10, 22, 3, 30, tzinfo=UTC))
    hk = instrument("0700.HK", engine.catalog)
    assert not is_open(hk, datetime(2026, 10, 22, 4, 0, tzinfo=UTC))
    order = engine.place(request(), NOW)
    quote = replace(sample(engine, order, offset=6), interval_start=NOW)
    engine.process(order.symbol, (quote,), NOW + timedelta(minutes=7))
    assert engine.store.read().orders[0].status == "accepted"


def test_sizing_includes_fee_and_existing_reservations(engine: Engine) -> None:
    body = request()
    size = size_order(engine, body, NOW, 50)
    assert size.max_quantity == 9900
    assert size.suggested_quantity == 4900
    _ = engine.place(body.model_copy(update={"quantity": size.max_quantity}), NOW)
    assert size_order(engine, request(), NOW, 100).max_quantity == 0


def test_same_effective_day_purchase_not_assumed_entitled(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    engine.process(order.symbol, (sample(engine, order),), NOW + timedelta(minutes=2))
    with pytest.raises(DomainError) as error:
        _ = apply_action(engine, action(engine), NOW + timedelta(minutes=3))
    assert error.value.code == "later_trades"


def test_sizing_uses_existing_currency_during_fx_outage(engine: Engine) -> None:
    engine.provider = ControlledProvider(fx_available=False)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(1000)
        engine.store.save(connection, state)
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 1})
    size = size_order(engine, body, NOW, 100)
    assert size.max_quantity == 9
    assert size.suggested_quantity == 9
    order = engine.place(body.model_copy(update={"quantity": size.suggested_quantity}), NOW)
    assert order.reserved_cny == 0
    assert order.reserved_foreign == Decimal("900.45")


def test_action_blocks_old_quote_and_preaction_cn_reference(engine: Engine) -> None:
    held(engine)
    _ = apply_action(engine, action(engine), NOW)
    with pytest.raises(DomainError) as error:
        _ = engine.place(request(), NOW)
    assert error.value.code == "action_price_pending"
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=5))
    with pytest.raises(DomainError) as error:
        _ = engine.place(request(), NOW + timedelta(minutes=5))
    assert error.value.code == "action_reference_pending"


def test_adjustment_between_prepare_and_place_is_rechecked(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    held(engine)
    original = engine.prepare

    def prepare_then_adjust(
        self: Engine, body: OrderInput, now: datetime
    ) -> tuple[Quote, FxRate | None, Decimal | None]:
        result = original(body, now)
        _ = apply_action(self, action(self), NOW)
        return result

    monkeypatch.setattr(Engine, "prepare", prepare_then_adjust)
    with pytest.raises(DomainError) as error:
        _ = engine.place(request().model_copy(update={"request_id": str(uuid4())}), NOW)
    assert error.value.code == "action_price_pending"
    assert engine.store.read().orders == []
