"""新增规则的数量、隔离及人工暂停边界。"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.activated_registry as registry_module
from app.catalog import instrument
from app.discovery import Discovery
from app.discovery_models import ObservationInstrument
from app.instrument_rules import InstrumentRule
from app.main import configured_engine, create_app
from app.marketdata import MarketDataClient, MarketDataError
from app.models import DomainError, OrderInput
from tests.admission_support import Clock
from tests.test_admission import inject, isolated, security_rule
from tests.test_api import PASSWORD
from tests.test_engine import NOW


@pytest.mark.parametrize("quantity", [199, 200, 201])
def test_star_buy_minimum_and_unit_increment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quantity: int,
) -> None:
    # Given: 科创规则200股起，每次1股递增。
    rule = security_rule("688981.SS")
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    _ = engine.registry.enable(rule.symbol, Discovery())
    request = OrderInput(
        request_id=str(uuid4()),
        symbol=rule.symbol,
        side="buy",
        order_type="limit",
        quantity=quantity,
        limit_price=Decimal(100),
    )
    # When / Then: 真实委托构建只拒绝低于最低量的请求。
    if quantity < 200:
        with pytest.raises(DomainError) as error:
            _ = engine.place(request, NOW)
        assert error.value.code == "invalid_lot"
    else:
        assert engine.place(request, NOW).quantity == quantity


def test_star_sell_rejects_small_partial_but_accepts_final_remainder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 科创真实买入301股，翌日可卖。
    rule = security_rule("688981.SS")
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    _ = engine.registry.enable(rule.symbol, Discovery())
    buy = OrderInput(
        request_id=str(uuid4()),
        symbol=rule.symbol,
        side="buy",
        order_type="limit",
        quantity=301,
        limit_price=Decimal(100),
    )
    _ = engine.place(buy, NOW)
    Clock.current += timedelta(minutes=6)
    engine.process(rule.symbol, engine.provider.intraday(rule.symbol), Clock.current)
    Clock.current += timedelta(days=1)
    # When: 部分卖出100股失败，201股成功；余下100股可以一次卖清。
    sell = buy.model_copy(update={"request_id": str(uuid4()), "side": "sell", "quantity": 100})
    with pytest.raises(DomainError) as error:
        _ = engine.place(sell, Clock.current)
    assert error.value.code == "invalid_lot"
    _ = engine.place(sell.model_copy(update={"quantity": 201}), Clock.current)
    Clock.current += timedelta(minutes=6)
    engine.process(rule.symbol, engine.provider.intraday(rule.symbol), Clock.current)
    _ = engine.place(sell.model_copy(update={"request_id": str(uuid4())}), Clock.current)
    Clock.current += timedelta(minutes=6)
    engine.process(rule.symbol, engine.provider.intraday(rule.symbol), Clock.current)
    # Then: 完整持仓清空且三笔订单已成交，数量约束没有绕过。
    state = engine.store.read()
    assert [order.status for order in state.orders] == ["filled", "filled", "filled"]
    assert sum(lot.quantity for lot in state.lots) == 0


def test_persisted_rule_pause_blocks_enable_preview_and_fill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 新标的已启用并有待成交订单；重新加载后应用人工暂停。
    rule = security_rule("600000.SS")
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    _ = engine.registry.enable(rule.symbol, Discovery())
    request = OrderInput(
        request_id=str(uuid4()),
        symbol=rule.symbol,
        side="buy",
        order_type="limit",
        quantity=100,
        limit_price=Decimal(100),
    )
    _ = engine.place(request, NOW)
    restarted = isolated(tmp_path)
    restarted.paused = frozenset({rule.symbol})
    Clock.current += timedelta(minutes=6)
    # When: 已持久化证券再次启用、预览、撮合。
    with TestClient(create_app(restarted, PASSWORD, run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert client.post(f"/api/instruments/{rule.symbol}/enable").status_code == 409
        assert client.post("/api/orders/preview", json=request.model_dump(mode="json")).status_code == 409
    restarted.process(rule.symbol, restarted.provider.intraday(rule.symbol), Clock.current)
    # Then: 暂停优先，旧单未成交，另一数据库也没有泄漏新目录。
    assert not instrument(rule.symbol, restarted.catalog).tradable
    assert restarted.store.read().orders[0].status == "accepted"
    assert not restarted.store.read().ledger
    other = isolated(tmp_path / "other")
    assert rule.symbol not in {item.symbol for item in other.catalog}


def test_configured_engine_restores_rule_and_applies_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 正式启动入口指向隔离数据库及持久化后的人工暂停代码。
    rule = security_rule("600000.SS")
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    _ = engine.registry.enable(rule.symbol, Discovery())
    monkeypatch.setenv("PAPER_DB", str(engine.store.path))
    monkeypatch.setenv("PAPER_PAUSED_SYMBOLS", rule.symbol)
    # When: 经真实配置入口重建服务引擎。
    restarted = configured_engine()
    # Then: 能解析动态证券，暂停覆盖持久启用标志，真实行情映射仍保留。
    assert not instrument(rule.symbol, restarted.catalog).tradable
    assert restarted.registry.resolve(rule.symbol).provider_code == rule.provider_code


@pytest.mark.parametrize(
    "code",
    [
        "unverified_etf_rules",
        "unsupported_provider_symbol",
        "unverified_instrument_maturity",
        "unverified_price_limit",
        "unsupported_instrument_status",
        "unsupported_instrument_class",
        "market_observation_only",
        "instrument_identity_changed",
    ],
)
def test_admission_policy_error_is_distinct_from_outage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    # Given: 供应商返回已识别的不支持原因，detail中混有不可直接公开的上游信息。
    rule = security_rule("600000.SS")
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)

    def reject(_item: ObservationInstrument, _client: MarketDataClient) -> InstrumentRule:
        raise MarketDataError(code, "https://upstream.invalid/private?token=secret")

    monkeypatch.setattr(registry_module, "verify_candidate", reject)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        # When: 经真实HTTP启用入口触发此限制。
        result = client.post(f"/api/instruments/{rule.symbol}/enable")
        # Then: 限制返回409及精确错误码，不伪装503故障，也不暴露原始上游内容。
        assert result.status_code == 409
        assert result.json()["detail"]["code"] == code
        assert "upstream.invalid" not in result.text
        assert "secret" not in result.text
    assert rule.symbol not in {item.symbol for item in isolated(tmp_path).catalog}
