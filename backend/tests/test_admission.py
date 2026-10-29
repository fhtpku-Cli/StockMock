"""通过真实 HTTP、SQLite 和后台 Worker 验证新增证券的完整交易生命周期。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.activated_registry as registry_module
import app.trading_routes as trading_module
import app.worker as worker_module
from app.activated_registry import RuleProvider
from app.admission_sources import candidate_rule
from app.catalog import CATALOG
from app.discovery import Discovery
from app.discovery_models import ObservationInstrument
from app.engine import Engine
from app.instrument_rules import CATALOG_RULES, InstrumentRule
from app.main import create_app
from app.marketdata import MarketDataClient, MarketDataError
from app.models import Instrument, OrderView
from app.storage import Store, read_rows
from app.worker import Worker
from tests.admission_support import Clock, RuleClient
from tests.test_api import PASSWORD
from tests.test_engine import NOW


def candidate(symbol: str = "600000.SS") -> InstrumentRule:
    base = next(rule for rule in CATALOG_RULES if rule.symbol == "600519.SS")
    return base.model_copy(update={"symbol": symbol, "provider_code": "sh" + symbol[:6], "name": "浦发银行"})


def isolated(tmp_path: Path) -> Engine:
    engine = Engine(Store(tmp_path / "admission.sqlite3"), RuleClient())
    engine.provider = RuleProvider(engine.registry, RuleClient())
    return engine


def inject(monkeypatch: pytest.MonkeyPatch, rule: InstrumentRule) -> None:
    def lookup(_self: Discovery, _symbol: str, _store: Store) -> ObservationInstrument:
        return ObservationInstrument(
            symbol=rule.symbol,
            name=rule.name,
            provider_code=rule.provider_code,
            market=rule.market,
            currency=rule.currency,
            kind=rule.kind,
        )

    def verify(_item: ObservationInstrument, _client: MarketDataClient) -> InstrumentRule:
        return rule

    monkeypatch.setattr(Discovery, "lookup", lookup)
    monkeypatch.setattr(registry_module, "verify_candidate", verify)
    monkeypatch.setattr(trading_module, "datetime", Clock)
    monkeypatch.setattr(worker_module, "datetime", Clock)
    monkeypatch.setattr(Clock, "current", NOW)


def security_rule(symbol: str) -> InstrumentRule:
    if symbol in {"159902.SZ", "159957.SZ", "SCHD"}:
        domestic = symbol.endswith(".SZ")
        return candidate_rule(
            ObservationInstrument(
                symbol=symbol,
                name="受控股票ETF",
                provider_code="sz" + symbol[:6] if domestic else "us" + symbol,
                market="CN" if domestic else "US",
                currency="CNY" if domestic else "USD",
                kind="ETF",
            )
        )
    rule = candidate(symbol)
    if symbol == "300750.SZ":
        rule = rule.model_copy(update={"provider_code": "sz300750", "price_limit_ratio": Decimal(".2")})
    if symbol == "688981.SS":
        rule = rule.model_copy(
            update={"lot_size": 1, "min_buy_quantity": 200, "quantity_step": 1, "price_limit_ratio": Decimal(".2")}
        )
    if symbol == "512880.SS":
        rule = rule.model_copy(update={"kind": "ETF", "tick_size": Decimal(".001")})
    if symbol == "PLTR":
        rule = rule.model_copy(
            update={
                "provider_code": "usPLTR",
                "market": "US",
                "currency": "USD",
                "lot_size": 1,
                "settlement_days": 0,
                "tick_rule": "US",
                "price_limit_ratio": None,
            }
        )
    return InstrumentRule.model_validate_json(rule.model_dump_json())


@pytest.mark.parametrize(
    "symbol", ["600000.SS", "300750.SZ", "688981.SS", "512880.SS", "PLTR", "159902.SZ", "159957.SZ", "SCHD"]
)
def test_new_security_http_worker_restart_sell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symbol: str) -> None:
    # Given: 新标的未在基础目录，真实数据库与行情规则适配器均隔离。
    rule = security_rule(symbol)
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    quantity = rule.min_buy_quantity or rule.lot_size
    if rule.market == "US":
        Clock.current = NOW.replace(hour=14)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        # When: 通过公开入口核验启用、预览和下单，后台无浏览器完成撮合。
        response = client.post(f"/api/instruments/{symbol}/enable", json={})
        assert response.status_code == 200, response.text
        assert Instrument.model_validate_json(response.content).tradable
        assert client.post(f"/api/instruments/{symbol}/enable", json={}).content == response.content
        payload = {
            "request_id": str(uuid4()),
            "symbol": symbol,
            "side": "buy",
            "order_type": "limit",
            "quantity": quantity,
            "limit_price": "100",
        }
        assert client.post("/api/orders/preview", json=payload).status_code == 200
        assert client.post("/api/orders/size?percent=25", json=payload).status_code == 200
        result = client.post("/api/orders", json=payload)
        assert result.status_code == 200, result.text
        buy = OrderView.model_validate_json(result.content)
    Clock.current += timedelta(minutes=10)
    restarted = isolated(tmp_path)
    Worker(restarted).tick()
    assert restarted.store.read().orders[0].status == "filled"
    assert restarted.store.read().lots[0].quantity == quantity
    with TestClient(create_app(restarted, PASSWORD, run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert client.get("/api/portfolio").status_code == 200
        assert client.get(f"/api/instruments/{symbol}/history").status_code == 200
        payload.update(request_id=str(uuid4()), side="sell")
        if rule.settlement_days:
            assert client.post("/api/orders", json=payload).status_code == 409
            Clock.current += timedelta(days=1)
        result = client.post("/api/orders", json=payload)
        assert result.status_code == 200, result.text
        sell = OrderView.model_validate_json(result.content)
        Clock.current += timedelta(minutes=10)
        Worker(restarted).tick()
        # Then: 新规则重启后仍可卖出、现金与持仓真实更新，基础目录未污染。
        state = restarted.store.read()
        assert all(order.status == "filled" for order in state.orders)
        assert sum(lot.quantity for lot in state.lots) == 0
        assert buy.id != sell.id
        assert len(state.ledger) >= 4
        rate = Decimal(7) if rule.currency == "USD" else Decimal(1)
        assert state.cash["CNY"] + state.cash["USD"] * 7 == Decimal(1000000) - Decimal(".1") * quantity * rate
        assert symbol not in {item.symbol for item in CATALOG}
        reset = client.post("/api/rounds/reset", json={"request_id": str(uuid4()), "confirmation": "重新开始"})
        assert reset.status_code == 200
        assert client.get(f"/api/rounds/{state.id}").status_code == 200
        assert client.get("/api/instruments", params={"q": symbol}).json()[0]["tradable"]


def test_enable_parallel_idempotence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 两个请求在同一次核验尚未完成时到达。
    rule = candidate()
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    entered, release = Event(), Event()
    calls: list[str] = []

    def verify(_item: ObservationInstrument, _client: MarketDataClient) -> InstrumentRule:
        calls.append(rule.symbol)
        entered.set()
        assert release.wait(5)
        return rule

    monkeypatch.setattr(registry_module, "verify_candidate", verify)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(engine.registry.enable, rule.symbol, Discovery())
        assert entered.wait(5)
        second = pool.submit(engine.registry.enable, rule.symbol, Discovery())
        release.set()
        # When / Then: 两个调用得到同一规则，仅调用一次供应商，SQLite只有一行。
        assert first.result() == second.result()
    assert calls == [rule.symbol]
    with engine.store.transaction() as connection:
        assert len(read_rows(connection, "SELECT data FROM activated_instruments")) == 1


def test_failure_and_policy_rejection_leave_no_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 供应商明确失败，另有人工暂停及港股限制。
    rule = candidate()
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)

    def fail(_item: ObservationInstrument, _client: MarketDataClient) -> InstrumentRule:
        raise MarketDataError("test_outage", "受控供应商失败")

    monkeypatch.setattr(registry_module, "verify_candidate", fail)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        assert client.post(f"/api/instruments/{rule.symbol}/enable").status_code == 401
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        # When: 失败、港股和暂停证券均尝试启用。
        assert client.post(f"/api/instruments/{rule.symbol}/enable").status_code == 503
        assert client.post("/api/instruments/0700.HK/enable").status_code == 409
        engine.paused = frozenset({rule.symbol})
        assert client.post(f"/api/instruments/{rule.symbol}/enable").status_code == 409
    # Then: 失败未留下可交易记录或改变全局目录。
    with engine.store.transaction() as connection:
        assert read_rows(connection, "SELECT data FROM activated_instruments") == []
    assert rule.symbol not in {item.symbol for item in isolated(tmp_path).catalog}
