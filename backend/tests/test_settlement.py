"""外币与持仓成本、交易日历的受控集成验证。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.api_models import ConvertInput
from app.calendar import window
from app.catalog import instrument
from app.engine import Engine
from app.models import DomainError, WorkerView
from app.operations import convert
from app.views import portfolio
from tests.test_engine import NOW, ControlledProvider, request, sample


def test_currency_buy_sell_retains_usd_and_reuses_it(engine: Engine) -> None:
    now = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    engine.provider = ControlledProvider(quote_time=now - timedelta(minutes=1))
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 10})
    buy = engine.place(body, now)
    engine.process("AAPL", (sample(engine, buy, "99"),), now + timedelta(minutes=2))
    state = engine.store.read()
    assert state.cash["CNY"] == Decimal("993066.50")
    assert state.cash["USD"] == 0
    engine.provider = ControlledProvider(quote_time=now + timedelta(minutes=2), fx_available=False)
    sell = engine.place(
        body.model_copy(update={"request_id": str(uuid4()), "side": "sell"}),
        now + timedelta(minutes=2),
    )
    engine.process("AAPL", (sample(engine, sell, "101"),), now + timedelta(minutes=4))
    assert engine.store.read().cash["USD"] == Decimal("1009.49")
    reuse = engine.place(body.model_copy(update={"request_id": str(uuid4())}), now + timedelta(minutes=4))
    assert reuse.reserved_cny == 0
    assert reuse.reserved_foreign == Decimal("1000.50")


def test_weighted_cost_after_two_buys_partial_sale(engine: Engine) -> None:
    now = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    engine.provider = ControlledProvider(quote_time=now)
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 10, "limit_price": Decimal(200)})
    for offset, price in [(0, "100"), (2, "200")]:
        buy = engine.place(
            body.model_copy(update={"request_id": str(uuid4())}),
            now + timedelta(minutes=offset),
        )
        engine.process("AAPL", (sample(engine, buy, price),), now + timedelta(minutes=offset + 1))
    sell = engine.place(
        body.model_copy(
            update={
                "request_id": str(uuid4()),
                "side": "sell",
                "limit_price": Decimal(100),
                "quantity": 5,
            }
        ),
        now + timedelta(minutes=4),
    )
    engine.process("AAPL", (sample(engine, sell, "150"),), now + timedelta(minutes=5))
    lots = engine.store.read().lots
    assert sum(lot.quantity for lot in lots) == 15
    assert sum(lot.cost for lot in lots) == Decimal("2251.12")


def test_us_daylight_saving_and_cn_holiday(engine: Engine) -> None:

    usa = instrument("AAPL", engine.catalog)
    summer, _ = window(usa, datetime(2026, 7, 1, 0, 0, tzinfo=UTC))
    winter, _ = window(usa, datetime(2026, 12, 1, 0, 0, tzinfo=UTC))
    china, _ = window(instrument("600519.SS", engine.catalog), datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
    assert summer.hour == 13
    assert winter.hour == 14
    assert china.date().isoformat() == "2026-10-08"


def test_stale_prior_close_rejects_daily_price_bound(engine: Engine) -> None:
    engine.provider = ControlledProvider(history_day=(NOW - timedelta(days=3)).date())
    with pytest.raises(DomainError) as error:
        _ = engine.place(request(), NOW)
    assert error.value.code == "reference_unavailable"
    assert engine.store.read().orders == []


def test_etf_average_cost_preserves_subcent_precision(engine: Engine) -> None:

    engine.provider = ControlledProvider(price=Decimal("4.189"))
    body = request().model_copy(update={"symbol": "510300.SS", "limit_price": Decimal("4.189")})
    buy = engine.place(body, NOW)
    engine.process(buy.symbol, (sample(engine, buy, "4.189"),), NOW + timedelta(minutes=2))
    view = portfolio(engine, NOW + timedelta(minutes=2), WorkerView())
    assert view.positions[0].average_cost == Decimal("4.1911")
    assert view.positions[0].average_cost * 100 == sum(lot.cost for lot in engine.store.read().lots)


def test_conversion_retry_survives_fx_outage(engine: Engine) -> None:

    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(100)
        engine.store.save(connection, state)
    body = ConvertInput(request_id=str(uuid4()), currency="USD", amount=Decimal(10))
    convert(engine, body, NOW)
    before = engine.store.read()
    engine.provider = ControlledProvider(fx_available=False)
    convert(engine, body, NOW)
    after = engine.store.read()
    assert after.cash == before.cash
    assert after.ledger == before.ledger
