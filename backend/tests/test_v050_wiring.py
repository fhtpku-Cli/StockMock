"""0.5.0 接线与真实地址：工厂路径只用临时 PAPER_DB，在 MarketDataClient 类上替换网络边界并记录地址。"""

import secrets
from dataclasses import fields
from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app import asset_routes, marketdata, views
from app.activated_registry import RuleProvider
from app.admission_sources import AdmissionSources
from app.api_models import HistoryView, QuoteView
from app.discovery import persist_observation
from app.discovery_models import ObservationInstrument
from app.main import configured_engine, configured_parts, create_app
from app.marketdata import MarketDataClient, MarketDataError, Quote
from app.models import DomainError, FillObservation, Lot, OrderInput, Portfolio
from tests.test_marketdata_reference import kline_raw, quote_raw

PASSWORD = secrets.token_urlsafe(24)
NOW = datetime.fromisoformat("2026-10-30T20:00:00+08:00").astimezone(UTC)
MINUTE = (
    b'var _data=([{"day":"2026-10-30 14:55:00","open":"100","high":"100","low":"100",'
    b'"close":"100.00","volume":"1000"}]);'
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """工厂默认相对路径在 backend/ 下就是正式库；本文件全部强制指向临时库。"""
    path = tmp_path / "isolated.sqlite3"
    monkeypatch.setenv("PAPER_DB", str(path))
    monkeypatch.delenv("PAPER_PAUSED_SYMBOLS", raising=False)
    return path


Responses = dict[str, bytes]


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> tuple[Responses, list[str]]:
    """按地址片段返回受控响应；未登记的地址明确失败，不连接真实网络。"""
    responses: Responses = {}
    urls: list[str] = []

    def fake(_self: MarketDataClient, url: str) -> bytes:
        urls.append(url)
        for key, body in responses.items():
            if key in url:
                return body
        raise MarketDataError("http_error", "404")

    monkeypatch.setattr(MarketDataClient, "_get", fake)
    return responses, urls


class Clock:
    current: ClassVar[datetime] = NOW

    @classmethod
    def now(cls, _tz: tzinfo | None = None) -> datetime:
        return cls.current


HISTORY = TypeAdapter(list[HistoryView])


def quote_view(client: TestClient, symbol: str) -> QuoteView:
    response = client.get(f"/api/instruments/{symbol}/quote")
    assert response.status_code == 200
    return QuoteView.model_validate_json(response.content)


def login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_configured_parts_returns_same_provider_instance() -> None:
    engine, display = configured_parts()
    assert engine.provider is display
    assert engine.plan_provider is not display
    assert isinstance(configured_engine().provider, RuleProvider)


def test_factory_path_wires_previous_close_everywhere(
    network: tuple[Responses, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    responses, _ = network
    monkeypatch.setattr(views, "CHANGE_DISABLED_MARKETS", frozenset[views.Market]())
    monkeypatch.setattr(asset_routes, "datetime", Clock)
    # 行情缓存记录真实取得时刻；固定到受控交易日，避免次日把当日日线误判为已定稿。
    monkeypatch.setattr(marketdata, "datetime", Clock)
    responses["q=sh600519"] = quote_raw("sh600519", "20261030150000", price="100", previous="98")
    responses["q=hk00700"] = quote_raw("hk00700", "2026/10/30 16:08:00", price="500", previous="490")
    responses["q=hk09618"] = quote_raw("hk09618", "2026/10/30 16:08:00", price="130", previous="125")
    responses["hk09618,day,,,252"] = kline_raw("hk09618", ["2026-10-29", "2026-10-30"])
    engine, _ = configured_parts()
    searched = ObservationInstrument(
        symbol="09618.HK", name="受控观察", market="HK", currency="HKD", kind="stock", provider_code="hk09618"
    )
    with engine.store.transaction() as connection:
        persist_observation(connection, searched)
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="600519.SS", quantity=100, cost=Decimal(9000), sellable_at=NOW))
        engine.store.save(connection, state)
    application = create_app(password=PASSWORD, run_worker=False)
    with TestClient(application) as client:
        login(client)
        tradable = quote_view(client, "600519.SS")
        base_hk = quote_view(client, "0700.HK")
        searched_hk = quote_view(client, "09618.HK")
        chart = HISTORY.validate_json(client.get("/api/instruments/09618.HK/history").content)
        position = Portfolio.model_validate_json(client.get("/api/portfolio").content).positions[0]
    assert (tradable.previous_close, tradable.change, tradable.change_status) == (
        Decimal(98),
        Decimal(2),
        "available",
    )
    assert (base_hk.previous_close, base_hk.change_status) == (Decimal(490), "available")
    assert (searched_hk.previous_close, searched_hk.change_status) == (Decimal(125), "available")
    assert [row.time for row in chart] == ["2026-10-29", "2026-10-30"]
    assert [row.complete for row in chart] == [True, False]
    assert all(row.retrieved_at is not None for row in chart)
    assert (position.previous_close, position.change_status) == (Decimal(98), "available")


def test_change_is_disabled_until_verification(
    network: tuple[Responses, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "CHANGE_DISABLED_MARKETS", frozenset({"HK"}))
    responses, _ = network
    responses["q=hk00700"] = quote_raw("hk00700", "2026/10/30 16:08:00", price="500", previous="490")
    with TestClient(create_app(password=PASSWORD, run_worker=False)) as client:
        login(client)
        body = quote_view(client, "0700.HK")
    assert (body.price, body.previous_close, body.change_status) == (Decimal(500), None, "market_disabled")


def test_quote_route_keeps_price_when_calendar_out_of_bounds(
    network: tuple[Responses, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    responses, _ = network
    monkeypatch.setattr(views, "CHANGE_DISABLED_MARKETS", frozenset[views.Market]())
    responses["q=sh600519"] = quote_raw("sh600519", "19900105100000", price="100", previous="98")
    with TestClient(create_app(password=PASSWORD, run_worker=False)) as client:
        login(client)
        response = client.get("/api/instruments/600519.SS/quote")
    assert response.status_code == 200
    body = QuoteView.model_validate_json(response.content)
    assert (body.price, body.tick_size, body.change, body.change_status) == (
        Decimal(100),
        Decimal("0.01"),
        None,
        "unknown",
    )


def test_quote_route_still_503_when_reference_fails(network: tuple[Responses, list[str]]) -> None:
    _ = network
    with TestClient(create_app(password=PASSWORD, run_worker=False)) as client:
        login(client)
        response = client.get("/api/instruments/600519.SS/quote")
    assert response.status_code == 503
    assert response.json()["detail"]["message"] == "外部行情暂不可用，未使用替代假数据"


def order_request() -> OrderInput:
    return OrderInput(
        request_id=str(uuid4()),
        symbol="600519.SS",
        side="buy",
        order_type="limit",
        quantity=100,
        limit_price=Decimal(100),
    )


def kline_urls(urls: list[str]) -> list[str]:
    return [url.rsplit("=", 1)[1] for url in urls if "kline" in url]


def test_cn_preorder_reference_uses_30_bar_url(network: tuple[Responses, list[str]]) -> None:
    responses, urls = network
    responses["getKLineData?symbol=sh600519"] = MINUTE
    responses["sh600519,day,,,30"] = kline_raw("sh600519", ["2026-10-29", "2026-10-30"])
    engine, _ = configured_parts()
    preview = engine.preview(order_request(), NOW)
    assert preview.price_bound == Decimal(100)
    assert kline_urls(urls) == ["sh600519,day,,,30"]


def test_cn_preorder_rejection_reason_unchanged(network: tuple[Responses, list[str]]) -> None:
    responses, urls = network
    responses["getKLineData?symbol=sh600519"] = MINUTE
    responses["sh600519,day,,,30"] = kline_raw("sh600519", ["2026-10-28", "2026-10-29"])
    engine, _ = configured_parts()
    with pytest.raises(DomainError) as rejected:
        _ = engine.preview(order_request(), NOW)
    assert (rejected.value.code, rejected.value.message) == ("reference_unavailable", "缺少前一交易日未复权收盘价")
    assert kline_urls(urls) == ["sh600519,day,,,30"]


def test_broken_chart_history_does_not_block_preorder(network: tuple[Responses, list[str]]) -> None:
    responses, urls = network
    responses["getKLineData?symbol=sh600519"] = MINUTE
    responses["sh600519,day,,,30"] = kline_raw("sh600519", ["2026-10-29", "2026-10-30"])
    responses["sh600519,day,,,252"] = kline_raw("sh600519", ["2026-10-30", "2026-10-29", "2026-10-30"])
    with TestClient(create_app(password=PASSWORD, run_worker=False)) as client:
        login(client)
        chart = client.get("/api/instruments/600519.SS/history")
    engine, _ = configured_parts()
    assert chart.status_code == 503
    assert b"invalid_chart_history" in chart.content
    assert engine.preview(order_request(), NOW).price_bound == Decimal(100)
    assert kline_urls(urls) == ["sh600519,day,,,252", "sh600519,day,,,30"]


def test_admission_maturity_history_request_and_parse_unchanged(network: tuple[Responses, list[str]]) -> None:
    responses, urls = network
    responses["sz000001,day,,,252"] = kline_raw("sz000001", ["2026-10-29", "2026-10-29", "2026-10-30"])
    item = ObservationInstrument(
        symbol="000001.SZ", name="受控", market="CN", currency="CNY", kind="stock", provider_code="sz000001"
    )
    bars = AdmissionSources().maturity_history(item)
    assert [bar.day.isoformat() for bar in bars] == ["2026-10-29", "2026-10-29", "2026-10-30"]
    assert kline_urls(urls) == ["sz000001,day,,,252"]


def test_fill_evidence_fields_are_unchanged() -> None:
    # 给 Quote 加字段会让 FillObservation(extra="forbid") 校验失败，所有成交都会失败。
    assert [item.name for item in fields(Quote)] == [
        "symbol",
        "price",
        "currency",
        "market_time",
        "retrieved_at",
        "source",
        "tradable",
        "interval_start",
        "volume",
    ]
    assert set(FillObservation.model_fields) == {
        "symbol",
        "currency",
        "source",
        "interval_start",
        "market_time",
        "retrieved_at",
        "price",
        "volume",
        "tradable",
    }
