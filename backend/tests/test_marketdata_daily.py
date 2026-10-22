"""自制解码函数验证隔离与边界，不包含新浪 SDK 或真实行情载荷。"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.marketdata_daily import decode_daily, extract_decoder, parse_daily
from app.marketdata_models import MarketDataError


def sdk_fixture(body: str) -> bytes:
    return (
        "this.xh5_S_KLC_D=function(t){var v=function(){return function(){" + body + "}};return v()()};var H={};"
    ).encode()


def test_decode_when_bounded_context_has_no_host_capabilities() -> None:
    # Given: 自制函数查询宿主对象；它不是供应商真实解码器。
    sdk = sdk_fixture("return [typeof std,typeof os,typeof fetch,typeof require,typeof process];")
    # When: 生产隔离解释器运行。
    result = decode_daily(b'var KLC_KL_AAPL="AAA";', sdk, "AAPL")
    # Then: 无文件、网络、Node 或宿主调用能力。
    assert json.loads(result) == ["undefined"] * 5


@pytest.mark.parametrize(
    "body",
    [
        "while(true){}",
        "let a=[];for(;;){a.push(new Array(100000).fill(1));}",
        "return require('fs').readFileSync('/etc/passwd');",
    ],
)
def test_decode_rejected_when_cpu_memory_or_host_access_exceeded(body: str) -> None:
    # Given: 远程函数可能失控或试图访问宿主。
    sdk = sdk_fixture(body)
    # When / Then: 超时、内存限制或不可用能力均显式失败。
    with pytest.raises(MarketDataError, match="daily_decode_failed"):
        _ = decode_daily(b'var KLC_KL_AAPL="AAA";', sdk, "AAPL")


@pytest.mark.parametrize("sdk", [b"other", b"this.xh5_S_KLC_D=function(x){}", b"x" * 1_000_001])
def test_source_rejected_when_structure_changes(sdk: bytes) -> None:
    # Given: 缺失、变化或过大的 SDK。
    # When / Then: 不尝试执行整个脚本或宽松寻找替代函数。
    with pytest.raises(MarketDataError, match="decoder_source_changed"):
        _ = extract_decoder(sdk)


def test_payload_rejected_when_symbol_mismatches() -> None:
    # Given: URL AAPL 却返回 MSFT 变量。
    sdk = sdk_fixture("return [];")
    # When / Then: 不把其他证券图表串到账户中。
    with pytest.raises(MarketDataError, match="invalid_daily_encoding"):
        _ = decode_daily(b'var KLC_KL_MSFT="AAA";', sdk, "AAPL")


def test_raw_prices_and_closed_days_when_history_contains_current_day() -> None:
    # Given: 自制跨拆股价格断层，当前日尚未收市。
    rows = [
        {"date": day, "open": price, "high": price, "low": price, "close": price, "volume": 10}
        for day, price in [
            ("2026-10-20T00:00:00Z", "400"),
            ("2026-10-21T00:00:00Z", "100"),
            ("2026-10-22T00:00:00Z", "101"),
        ]
    ]
    # When: 解析图表日线。
    bars = parse_daily(json.dumps(rows), "AAPL", datetime(2026, 10, 22, 15, tzinfo=UTC))
    # Then: 断层保留、不复权，不展示尚未收市的日条。
    assert [bar.close for bar in bars] == [Decimal(400), Decimal(100)]
    assert bars[-1].market_time == datetime(2026, 10, 21, tzinfo=UTC)


@pytest.mark.parametrize("stamp", ["2026-10-21T00:00:00", "2026-10-21T00:00:00+08:00", "2026-10-21T09:30:00Z"])
def test_date_rejected_when_not_utc_date_label(stamp: str) -> None:
    # Given: 解码结果不再符合日期标签协议。
    raw = json.dumps([{"date": stamp, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 10}])
    # When / Then: 拒绝把未知时间语义带入图表。
    with pytest.raises(MarketDataError, match="invalid_daily_date"):
        _ = parse_daily(raw, "SPY", datetime(2026, 10, 22, tzinfo=UTC))
