"""通过真实 HTTP 路由和临时 SQLite 验证预览汇率与只读行为；行情为受控数据。"""

from collections.abc import Generator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Final, Literal, override

import pytest
from fastapi.testclient import TestClient

import app.trading_routes as routes_module
from app.engine import Engine
from app.main import create_app
from app.marketdata import FxRate, MarketDataError
from app.models import Lot, Preview
from app.storage import read_rows
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request

RATE: Final = FxRate("USD", Decimal(7), NOW, NOW, "controlled-fx")
UNAVAILABLE_RATES: Final = [None, (), (replace(RATE, market_time=NOW - timedelta(days=6)),)]


@dataclass(frozen=True, slots=True)
class PreviewProvider(ControlledProvider):
    rates: tuple[FxRate, ...] | None = (RATE,)

    @override
    def fx_rates(self) -> tuple[FxRate, ...]:
        if self.rates is None:
            raise MarketDataError("test_outage", "受控汇率失败")
        return self.rates


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    class MarketClock:
        @staticmethod
        def now(_tz: tzinfo | None = None) -> datetime:
            return NOW

    monkeypatch.setattr(routes_module, "datetime", MarketClock)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as session:
        assert session.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield session


@pytest.mark.parametrize("foreign_cash", [Decimal(0), Decimal(100)])
def test_preview_reports_real_rate_when_conversion_needed(
    engine: Engine, client: TestClient, foreign_cash: Decimal
) -> None:
    # Given: 真实临时账本有零或部分美元，有效汇率来自受控供应商。
    engine.provider = PreviewProvider()
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = foreign_cash
        engine.store.save(connection, state)
    before = engine.store.read()
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 10})

    # When: 从 HTTP 入口预览需要自动换汇的买单。
    response = client.post("/api/orders/preview", json=body.model_dump(mode="json"))

    # Then: 只报告真实汇率及出处，账户与请求记录均不改变。
    assert response.status_code == 200, response.text
    result = Preview.model_validate_json(response.content)
    assert result.fx_rate == Decimal(7)
    assert result.fx_source == "controlled-fx"
    assert result.fx_time == NOW
    assert result.reserved_cny == (Decimal("1000.50") - foreign_cash) * 7
    assert result.reserved_foreign == foreign_cash
    assert engine.store.read() == before
    with engine.store.transaction() as connection:
        assert read_rows(connection, "SELECT id FROM requests") == []


@pytest.mark.parametrize(
    "scenario",
    [
        ("sell", None),
        ("buy", None),
        ("buy", ()),
        ("buy", (replace(RATE, market_time=NOW - timedelta(days=6)),)),
        ("buy", (RATE,)),
    ],
    ids=["sell-outage", "buy-outage", "buy-missing", "buy-stale", "buy-valid-unused"],
)
def test_preview_omits_rate_when_conversion_not_needed(
    engine: Engine,
    client: TestClient,
    scenario: tuple[Literal["buy", "sell"], tuple[FxRate, ...] | None],
) -> None:
    # Given: 足额美元和可卖持仓；汇率状态不影响原币交易。
    side, rates = scenario
    engine.provider = PreviewProvider(rates=rates)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal("1000.50")
        state.lots.append(Lot(symbol="AAPL", quantity=10, cost=Decimal(1000), sellable_at=NOW))
        engine.store.save(connection, state)
    before = engine.store.read()
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 10, "side": side})

    # When: 预览不需要自动换汇的外币委托。
    response = client.post("/api/orders/preview", json=body.model_dump(mode="json"))

    # Then: 不虚构汇率、出处或时间，预览不落账。
    assert response.status_code == 200, response.text
    result = Preview.model_validate_json(response.content)
    assert result.fx_rate is None
    assert result.fx_source is None
    assert result.fx_time is None
    assert result.reserved_cny == 0
    assert engine.store.read() == before
    with engine.store.transaction() as connection:
        assert read_rows(connection, "SELECT id FROM requests") == []


@pytest.mark.parametrize("rates", UNAVAILABLE_RATES, ids=["outage", "missing", "stale"])
def test_preview_rejects_currency_shortfall_when_rate_unavailable(
    engine: Engine, client: TestClient, rates: tuple[FxRate, ...] | None
) -> None:
    # Given: 美元不足，需要兑换，但汇率失败、缺失或过期。
    engine.provider = PreviewProvider(rates=rates)
    before = engine.store.read()
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 10})

    # When: 通过相同 HTTP 入口预览。
    response = client.post("/api/orders/preview", json=body.model_dump(mode="json"))

    # Then: 保持既有拒绝行为，无资金、委托、流水或幂等记录变更。
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "fx_unavailable"
    assert engine.store.read() == before
    with engine.store.transaction() as connection:
        assert read_rows(connection, "SELECT id FROM requests") == []


def test_preview_keeps_real_rate_when_small_conversion_rounds_up(engine: Engine, client: TestClient) -> None:
    engine.provider = PreviewProvider(rates=(replace(RATE, cny_per_unit=Decimal("0.000001")),))
    before = engine.store.read()
    body = request().model_copy(update={"symbol": "AAPL", "quantity": 1, "limit_price": Decimal("0.01")})

    response = client.post("/api/orders/preview", json=body.model_dump(mode="json"))

    assert response.status_code == 200, response.text
    result = Preview.model_validate_json(response.content)
    assert result.reserved_cny == Decimal("0.01")
    assert result.fx_rate == Decimal("0.000001")
    assert result.fx_source == "controlled-fx"
    assert result.fx_time == NOW
    assert engine.store.read() == before


def test_preview_omits_exchange_rate_for_cny_order(engine: Engine, client: TestClient) -> None:
    engine.provider = PreviewProvider(rates=None)
    before = engine.store.read()

    response = client.post("/api/orders/preview", json=request().model_dump(mode="json"))

    assert response.status_code == 200, response.text
    result = Preview.model_validate_json(response.content)
    assert result.reserved_cny == Decimal("10005.00")
    assert result.fx_rate is None
    assert result.fx_source is None
    assert result.fx_time is None
    assert engine.store.read() == before
