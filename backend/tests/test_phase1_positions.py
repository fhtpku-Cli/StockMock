"""通过预览 HTTP 入口验证估值重用、精度与降级；只使用受控行情。"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, override

import pytest
from fastapi.testclient import TestClient

from app.engine import Engine
from app.marketdata import Bar, FxRate, MarketDataError, Quote
from app.models import Lot, OrderInput, Portfolio, Preview
from tests.round_support import MarketClock
from tests.test_engine import NOW, ControlledProvider, request


@dataclass(frozen=True, slots=True)
class Counted(ControlledProvider):
    hits: Counter[str] = field(default_factory=Counter)
    missing_symbol: str = ""

    @override
    def quote(self, symbol: str) -> Quote:
        self.hits["quote"] += 1
        if symbol == self.missing_symbol:
            raise MarketDataError("test_missing", "受控缺价")
        return super(Counted, self).quote(symbol)

    @override
    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        self.hits["intraday"] += 1
        return (super(Counted, self).quote(symbol),)

    @override
    def history(self, symbol: str, *, retrieved_not_before: datetime | None = None) -> tuple[Bar, ...]:
        self.hits["history"] += 1
        return ControlledProvider().history(symbol, retrieved_not_before=retrieved_not_before)

    @override
    def fx_rates(self) -> tuple[FxRate, ...]:
        self.hits["fx_rates"] += 1
        return super(Counted, self).fx_rates()


def get_preview(api: TestClient, body: OrderInput) -> Preview:
    response = api.post("/api/orders/preview", json=body.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    return Preview.model_validate_json(response.content)


def get_portfolio(api: TestClient) -> Portfolio:
    response = api.get("/api/portfolio")
    assert response.status_code == 200, response.text
    return Portfolio.model_validate_json(response.content)


@pytest.mark.parametrize(
    "case",
    [
        (0, "buy", 100, "0.00", "50.01"),
        (200, "buy", 100, "50.00", "75.01"),
        (200, "sell", 100, "50.00", "25.00"),
        (200, "sell", 200, "50.00", "0.00"),
    ],
)
def test_cny_complete_weights(
    engine: Engine, api: TestClient, case: tuple[int, Literal["buy", "sell"], int, str, str]
) -> None:
    held, side, quantity, current, after = case
    body = request().model_copy(update={"side": side, "quantity": quantity, "stop_price": Decimal(90)})
    provider = Counted()
    engine.provider = provider
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["CNY"] = Decimal(20000)
        if held:
            state.lots.append(Lot(symbol=body.symbol, quantity=held, cost=Decimal(20000), sellable_at=NOW))
        engine.store.save(connection, state)
    before = get_portfolio(api)
    provider.hits.clear()
    preview = get_preview(api, body)
    impact = preview.position_impact
    assert impact is not None
    assert impact.status == "complete"
    assert impact.current_percent == Decimal(current)
    assert impact.after_percent == Decimal(after)
    if before.positions:
        weight = before.positions[0].weight_percent
        assert weight is not None
        assert impact.current_percent == weight.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    assert provider.hits == {"intraday": 1, "history": 1}
    if side == "buy":
        assert preview.stop_loss is not None
        assert preview.stop_loss.cny == Decimal("1009.50")
        assert preview.stop_loss.asset_percent == Decimal("2.52" if held else "5.05")
    else:
        assert preview.stop_loss is None


def test_first_us_purchase_conversion(engine: Engine, api: TestClient) -> None:
    provider = Counted()
    engine.provider = provider
    _ = get_portfolio(api)
    provider.hits.clear()
    body = request().model_copy(update={"symbol": "AAPL", "stop_price": Decimal(90)})
    preview = get_preview(api, body)
    impact = preview.position_impact
    assert impact is not None
    assert (impact.current_percent, impact.after_percent) == (0, Decimal("7.00"))
    assert impact.fx_source == "预览汇率"
    assert impact.fx_time == NOW
    assert preview.stop_loss is not None
    assert preview.stop_loss.cny == Decimal("7066.50")
    assert preview.stop_loss.asset_percent == Decimal("0.71")
    assert provider.hits == {"intraday": 1, "fx_rates": 1}


@pytest.mark.parametrize("mode", ["empty", "expired", "changed", "incomplete", "missing_fx"])
def test_fallback_keeps_valid_order(
    engine: Engine, api: TestClient, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    provider = Counted(missing_symbol="AAPL" if mode == "incomplete" else "", fx_available=mode != "missing_fx")
    engine.provider = provider
    body = request().model_copy(update={"stop_price": Decimal(90)})
    if mode == "incomplete":
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.lots.append(Lot(symbol="AAPL", quantity=1, cost=Decimal(100), sellable_at=NOW))
            engine.store.save(connection, state)
    if mode == "missing_fx":
        body = body.model_copy(update={"symbol": "AAPL"})
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.cash["USD"] = Decimal(20000)
            engine.store.save(connection, state)
    if mode != "empty":
        _ = get_portfolio(api)
    if mode == "expired":
        monkeypatch.setattr(MarketClock, "current", NOW + timedelta(seconds=61))
    if mode == "changed":
        _ = engine.place(request(), NOW)
    provider.hits.clear()
    preview = get_preview(api, body)
    impact = preview.position_impact
    assert impact is not None
    assert impact.status == (mode if mode in {"incomplete", "missing_fx"} else "expired")
    assert impact.current_percent is None
    assert preview.stop_loss is not None
    assert preview.stop_loss.asset_percent is None
    if mode == "missing_fx":
        assert impact.reserved_foreign_percent == Decimal("50.03")
        assert preview.stop_loss.cny is None
    else:
        assert preview.stop_loss.cny == Decimal("1009.50")
        assert impact.reserved_cny_percent == Decimal("1.01" if mode == "changed" else "1.00")
    assert provider.hits == ({"intraday": 1, "fx_rates": 1} if mode == "missing_fx" else {"intraday": 1, "history": 1})
    assert api.post("/api/orders", json=body.model_dump(mode="json")).status_code == 200


def test_cached_rate_takes_precedence(engine: Engine, api: TestClient) -> None:
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(5000)
        engine.store.save(connection, state)
    _ = get_portfolio(api)
    engine.provider = Counted(fx_available=False)
    preview = get_preview(
        api, request().model_copy(update={"symbol": "AAPL", "quantity": 1, "stop_price": Decimal(90)})
    )
    assert preview.stop_loss is not None
    assert preview.stop_loss.cny == Decimal("70.70")
    assert preview.position_impact is not None
    assert preview.position_impact.fx_source == "估值汇率"


def test_sell_fallback_remaining_and_cache_boundary(
    engine: Engine, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = request().model_copy(update={"side": "sell", "quantity": 100})
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol=body.symbol, quantity=300, cost=Decimal(30000), sellable_at=NOW))
        engine.store.save(connection, state)
    _ = engine.place(request(), NOW)
    _ = get_portfolio(api)
    monkeypatch.setattr(MarketClock, "current", NOW + timedelta(seconds=60))
    fresh = get_preview(api, body).position_impact
    assert fresh is not None
    assert fresh.status == "complete"
    assert fresh.active_orders == 1
    monkeypatch.setattr(MarketClock, "current", NOW + timedelta(seconds=61))
    expired = get_preview(api, body).position_impact
    assert expired is not None
    assert expired.status == "expired"
    assert expired.remaining_quantity == 200


def test_cash_price_and_holding_price_are_distinct(engine: Engine, api: TestClient) -> None:
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["CNY"] = Decimal(20000)
        state.lots.append(Lot(symbol="600519.SS", quantity=200, cost=Decimal(19000), sellable_at=NOW))
        engine.store.save(connection, state)
    engine.provider = ControlledProvider(price=Decimal(105))
    _ = get_portfolio(api)
    engine.provider = ControlledProvider(price=Decimal(100))
    limit = get_preview(api, request().model_copy(update={"limit_price": Decimal(95)}))
    market = get_preview(api, request().model_copy(update={"order_type": "market"}))
    assert limit.position_impact is not None
    assert market.position_impact is not None
    # 同一估值持仓市值 21000；加仓后 31500。限价单现金减少 9504.75，市价单现金减少 10005.10。
    assert limit.position_impact.after_percent == Decimal("75.01")
    assert market.position_impact.after_percent == Decimal("75.91")


def test_stop_loss_uses_execution_rounding(api: TestClient) -> None:
    # 止损价乘一股先按分记为 10.00，真实卖出费用由半分进至 0.01。
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 1, "stop_price": Decimal("9.9999")})
    preview = get_preview(api, body)
    assert preview.stop_loss is not None
    assert preview.stop_loss.amount == Decimal("90.06")
