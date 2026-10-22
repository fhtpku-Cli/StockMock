"""目录导入、错误代码映射与港股价位边界的受控验证。"""

from datetime import UTC, date, datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Literal, final, override

import pytest
from pydantic import TypeAdapter

from app.instrument_import import ImportEntry, import_catalog
from app.instrument_registry import catalog_rules, validate_rule
from app.instrument_rules import CATALOG_RULES, InstrumentRule
from app.market_ticks import hk_tick_size
from app.marketdata import MarketDataClient
from app.marketdata_models import MarketDataError


@pytest.mark.parametrize(
    ("price", "kind", "tick"),
    [
        (".25", "stock", ".001"),
        (".255", "stock", ".005"),
        ("10", "stock", ".005"),
        ("10.01", "stock", ".01"),
        ("20", "stock", ".01"),
        ("20.02", "stock", ".02"),
        ("100", "stock", ".05"),
        ("100.1", "stock", ".1"),
        ("1", "ETF", ".001"),
        ("1.002", "ETF", ".002"),
        ("28.5", "ETF", ".02"),
        ("28.5", "stock", ".02"),
    ],
)
def test_tick_when_price_crosses_band(price: str, kind: Literal["stock", "ETF"], tick: str) -> None:
    # Given: 交易所边界价或紧接下一档的价格。
    value = Decimal(price)
    # When: 选择股票或独立 ETF 价位表。
    result = hk_tick_size(value, kind)
    # Then: 价位属于正确档，不能用固定 .01。
    assert result == Decimal(tick)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "9996"])
def test_tick_rejected_when_price_unsupported(value: str) -> None:
    # Given: 不属于港交所股票价格域。
    price = Decimal(value)
    # When / Then: 明确失败，不猜测价位。
    with pytest.raises(MarketDataError, match="unsupported_price"):
        _ = hk_tick_size(price)


def test_rule_rejected_when_provider_refers_to_other_symbol() -> None:
    # Given: 显示 AAPL 但实际请求 MSFT 的危险配置。
    rule = next(row for row in CATALOG_RULES if row.symbol == "AAPL").model_copy(update={"provider_code": "usMSFT"})
    # When / Then: 从源头拒绝错误证券归属。
    with pytest.raises(MarketDataError, match="invalid_instrument_rule"):
        _ = validate_rule(rule)


def test_registry_rejected_when_alias_duplicates_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 00700.HK 与内置 0700.HK 指向同一个供应商代码。
    rule = next(row for row in CATALOG_RULES if row.symbol == "0700.HK").model_copy(update={"symbol": "00700.HK"})
    path = tmp_path / "catalog.json"
    _ = path.write_bytes(TypeAdapter(tuple[InstrumentRule, ...]).dump_json((rule,)))
    monkeypatch.setenv("STOCKMOCK_INSTRUMENTS_FILE", str(path))
    catalog_rules.cache_clear()
    # When / Then: 加载不能建立第二个证券账本。
    try:
        with pytest.raises(MarketDataError, match="duplicate_provider_alias"):
            _ = catalog_rules()
    finally:
        catalog_rules.cache_clear()


def test_import_no_output_when_duplicate_rules(tmp_path: Path) -> None:
    # Given: 重复配置在调用外部行情之前就可判定。
    entry = ImportEntry(
        rule=CATALOG_RULES[0],
        listing_date=date(2001, 8, 27),
        status="normal",
        product="ordinary_stock",
        metadata_source="https://www.sse.com.cn/",
    )
    source, destination = tmp_path / "input.json", tmp_path / "output.json"
    _ = source.write_bytes(TypeAdapter(tuple[ImportEntry, ...]).dump_json((entry, entry)))
    # When / Then: 失败不生成半份输出。
    with pytest.raises(MarketDataError, match="duplicate_instrument"):
        _ = import_catalog(source, destination, MarketDataClient())
    assert not destination.exists()


@final
class _Clock(datetime):
    @classmethod
    @override
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 22, 5, tzinfo=UTC)


def test_import_when_explicit_metadata_and_real_parser_agree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 仅替代网络字节和时钟；真实解析器、导入器和落盘全部执行。
    monkeypatch.setattr("app.instrument_import.datetime", _Clock)
    monkeypatch.setattr("app.marketdata.datetime", _Clock)
    rule = next(row for row in CATALOG_RULES if row.symbol == "AAPL").model_copy(
        update={"symbol": "COST", "provider_code": "usCOST", "name": "Costco", "tradable": False}
    )
    entry = ImportEntry(
        rule=rule,
        listing_date=date(1985, 12, 5),
        status="normal",
        product="ordinary_stock",
        metadata_source="https://investor.costco.com/stock-info/default.aspx",
    )
    source, destination = tmp_path / "input.json", tmp_path / "output.json"
    _ = source.write_bytes(TypeAdapter(tuple[ImportEntry, ...]).dump_json((entry,)))
    fields = [""] * 31
    fields[3], fields[30] = "99.5", "2026-10-21 16:00:01"
    raw = f'v_usCOST="{"~".join(fields)}";'.encode()

    def respond(_self: MarketDataClient, _url: str) -> bytes:
        return raw

    monkeypatch.setattr(MarketDataClient, "_get", respond)
    # When: 完整导入操作。
    count = import_catalog(source, destination, MarketDataClient())
    # Then: 输出可被运行时相同边界读取，保留观察模式。
    loaded = TypeAdapter(tuple[InstrumentRule, ...]).validate_json(destination.read_bytes())
    assert (count, loaded[0].symbol, loaded[0].tradable) == (1, "COST", False)
