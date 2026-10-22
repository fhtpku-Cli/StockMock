"""受控搜索响应与临时账户验证，不使用正式数据库。"""

import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.discovery import Discovery, parse_search, parse_us_search
from app.discovery_hk import hk_categories
from app.discovery_models import ObservationInstrument, SearchResult
from app.engine import Engine
from app.main import create_app
from app.marketdata_models import MarketDataError
from app.models import DomainError
from tests.test_api import PASSWORD


def response(rows: str) -> bytes:
    return ("v_hint=" + json.dumps(rows)).encode()


ROWS = (
    "sh~688981~中芯国际~zxgj~GP-A-KCB^hk~00981~中芯国际~zxgj~GP^"
    "sh~512660~军工ETF~jg~ETF^sh~113050~南银转债~nyzz~ZQ^"
    "jj~000001~基金~jj~KJ^sh~000001~上证指数~szzs~ZS^"
    "hk~12345~认购权证~rg~WARRANT^sh~123456~未知产品~wz~UNKNOWN"
)


def hk_response(
    code: str = "00981", category: str = "Equity", subcategory: str = "Equity Securities (Main Board)"
) -> bytes:
    rows = [("Stock Code", "Category", "Sub-Category", "Trading Currency"), (code, category, subcategory, "HKD")]
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for number, values in enumerate(rows, 1):
        xml += (
            "<row>"
            + "".join(
                f'<c r="{column}{number}"><v>{value}</v></c>'
                for column, value in zip(("A", "C", "D", "Q"), values, strict=True)
            )
            + "</row>"
        )
    xml += "</sheetData></worksheet>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>'
        )
        archive.writestr("xl/worksheets/sheet1.xml", xml)
    return buffer.getvalue()


def test_parser_accepts_only_target_types() -> None:
    # Given: 目标证券和非目标类别的受控响应。
    raw = response(ROWS)
    # When: 解析。
    items = parse_search(raw)
    # Then: 仅股票及 ETF，未知规则留空。
    assert {i.symbol for i in items} == {"688981.SS", "00981.HK", "512660.SS"}
    assert all(not i.tradable and i.lot_size is None and i.tick_size is None for i in items)


def test_us_classification_rejects_unknown_and_labels_etf() -> None:
    rows = [
        {
            "Code": symbol,
            "Name": symbol,
            "Classify": "UsStock",
            "SecurityType": "20" if symbol == "ADBE" else "7",
            "TypeUS": kind,
            "JYS": "NASDAQ",
        }
        for symbol, kind in [("ADBE", "1"), ("TESTETF", "5"), ("WARRANT", "9")]
    ]
    raw = json.dumps({"QuotationCodeTable": {"Data": rows, "Status": 0}}).encode()
    items = parse_us_search(raw)
    assert [(i.symbol, i.kind) for i in items] == [("ADBE", "stock"), ("TESTETF", "ETF")]


def test_search_encodes_and_coalesces_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def get(_self: Discovery, url: str) -> bytes:
        calls.append(url)
        return hk_response() if "ListOfSecurities" in url else response(ROWS)

    monkeypatch.setattr(Discovery, "_get", get)
    client = Discovery()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(client.search, ["中芯 & ?"] * 8))
    assert len(calls) == 2
    assert "q=%E4%B8%AD%E8%8A%AF+%26+%3F&t=all" in calls[0]
    assert all(len(result) == 3 for result in results)


def test_empty_and_invalid_search_never_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_self: Discovery, _url: str) -> bytes:
        pytest.fail("空输入不应联网")

    monkeypatch.setattr(Discovery, "_get", fail)
    client = Discovery()
    assert client.search("   ") == ()
    with pytest.raises(DomainError):
        _ = client.search("x" * 81)
    with pytest.raises(DomainError):
        _ = client.search("x\ny")


def test_failure_and_cache_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fail(_self: Discovery, _url: str) -> bytes:
        calls.append(_url)
        raise MarketDataError("network_error", "受控故障")

    monkeypatch.setattr(Discovery, "_get", fail)
    client = Discovery()
    for number in range(300):
        with pytest.raises(MarketDataError, match="network_error"):
            _ = client.search(str(number))
    with pytest.raises(MarketDataError):
        _ = client.search("0")
    assert len(calls) == 301


def test_http_discovery_persists_observation_without_trade_eligibility(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(_self: Discovery, _url: str) -> bytes:
        return hk_response() if "ListOfSecurities" in _url else response(ROWS)

    monkeypatch.setattr(Discovery, "_get", get)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        assert client.get("/api/instruments/search?q=688981").status_code == 401
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        result = SearchResult.model_validate_json(client.get("/api/instruments/search?q=688981").content)
        assert "688981.SS" in {item.symbol for item in result.items}
        assert (
            client.post(
                "/api/watchlist", json={"symbol": "688981.SS"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.post("/api/watchlist", json={"symbol": "688981.SS"}).status_code == 200
        order = client.post(
            "/api/orders/preview",
            json={
                "symbol": "688981.SS",
                "side": "buy",
                "quantity": 100,
                "request_id": "observation-test",
                "order_type": "market",
            },
        )
        assert order.status_code in {404, 409}
        assert engine.store.read().orders == []
    # 新应用实例与缓存重建，持久观察元数据仍可读取。
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        watch = client.get("/api/watchlist")
        assert watch.status_code == 200
        item = ObservationInstrument.model_validate(watch.json()[0])
        assert item.symbol == "688981.SS"
        assert item.tradable is False


def test_http_search_failure_is_explicit_and_known_rules_win(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    def get(_self: Discovery, _url: str) -> bytes:
        return response("sh~600519~供应商名称~name~GP-A")

    monkeypatch.setattr(Discovery, "_get", get)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        result = SearchResult.model_validate_json(client.get("/api/instruments/search?q=600519").content)
        original = next(item for item in engine.catalog if item.symbol == "600519.SS")
        assert result.items == [original]

    def fail(_self: Discovery, _url: str) -> bytes:
        raise MarketDataError("network_error", "受控故障")

    monkeypatch.setattr(Discovery, "_get", fail)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        result = client.get("/api/instruments/search?q=688981")
        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "network_error"


def test_us_search_requires_exact_identity_across_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    def get(_self: Discovery, url: str) -> bytes:
        if "smartbox" in url:
            return response("us~adbe.oq~adobe~*~GP")
        return json.dumps(
            {
                "QuotationCodeTable": {
                    "Data": [
                        {
                            "Code": "WRONG",
                            "Name": "adobe",
                            "Classify": "UsStock",
                            "SecurityType": "7",
                            "TypeUS": "1",
                            "JYS": "NASDAQ",
                        },
                    ],
                    "Status": 0,
                }
            }
        ).encode()

    monkeypatch.setattr(Discovery, "_get", get)
    assert Discovery().search("ADBE") == ()


def test_known_alias_remains_searchable_when_supplier_is_empty(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    def get(_self: Discovery, _url: str) -> bytes:
        return response("")

    monkeypatch.setattr(Discovery, "_get", get)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        for query in ("AAPL", "苹果"):
            result = SearchResult.model_validate_json(
                client.get("/api/instruments/search", params={"q": query}).content
            )
            assert any(item.symbol == "AAPL" and item.tradable for item in result.items)


def test_observation_symbol_cannot_enter_trading_intraday() -> None:
    item = parse_search(response(ROWS))[0]
    with pytest.raises(MarketDataError, match="unsupported_symbol"):
        _ = Discovery().intraday(item.symbol)


def test_hk_category_is_confirmed_by_official_catalog() -> None:
    assert hk_categories(hk_response("02822", "Exchange Traded Products", "Exchange Traded Funds")) == {"02822": "ETF"}
    assert hk_categories(hk_response()) == {"00981": "stock"}


def test_hk_reit_and_changed_format_are_rejected() -> None:
    with pytest.raises(MarketDataError, match="invalid_hk_catalog"):
        _ = hk_categories(hk_response("00823", "Real Estate Investment Trusts", "REIT"))
    with pytest.raises(MarketDataError, match="invalid_hk_catalog"):
        _ = hk_categories(b"invalid zip")
    with pytest.raises(MarketDataError, match="清单压缩包超出上限"):
        _ = hk_categories(b"x" * 2_000_001)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", b"x" * 30_000_001)
        archive.writestr("xl/worksheets/sheet1.xml", b"<x/>")
    with pytest.raises(MarketDataError, match="清单解压总量超出上限"):
        _ = hk_categories(buffer.getvalue())


def test_normal_null_search_result_is_empty() -> None:
    raw = b'{"QuotationCodeTable":{"Data":null,"Status":0,"TotalCount":0}}'
    assert parse_us_search(raw) == ()


def test_hk_aliases_preserve_catalog_and_single_watch(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    def get(_self: Discovery, url: str) -> bytes:
        return (
            hk_response("03033", "Exchange Traded Products", "Exchange Traded Funds")
            if "ListOfSecurities" in url
            else response("hk~03033~南方恒生科技~nf~GP")
        )

    monkeypatch.setattr(Discovery, "_get", get)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        result = SearchResult.model_validate_json(client.get("/api/instruments/search?q=南方恒生科技").content)
        assert [item.symbol for item in result.items] == ["3033.HK"]
        assert result.items[0].kind == "ETF"
        assert result.items[0].lot_size == 200
        assert client.post("/api/watchlist", json={"symbol": "03033.HK"}).status_code == 200
        assert client.post("/api/watchlist", json={"symbol": "3033.HK"}).status_code == 200
        assert engine.store.read().watchlist == ["3033.HK"]


def test_hk_official_xml_rejects_changed_header_and_entities() -> None:
    for replacement, reason in (
        (b"<worksheet/>", "官方清单字段或类别已改变"),
        (b'<!DOCTYPE x [<!ENTITY x "expanded">]><x>&x;</x>', "清单含不允许的 XML 声明"),
    ):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/sharedStrings.xml", b"<sst/>")
            archive.writestr("xl/worksheets/sheet1.xml", replacement)
        with pytest.raises(MarketDataError, match=reason):
            _ = hk_categories(buffer.getvalue())
