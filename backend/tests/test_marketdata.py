"""全部为受控夹具；真实公共网络请求证据在 work/data_probe。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event
from typing import final, override

import pytest

from app.marketdata import (
    MarketDataClient,
    MarketDataError,
    parse_fx,
    parse_history,
    parse_intraday,
    parse_quote,
)

NOW = datetime(2026, 10, 21, 20, 50, tzinfo=UTC)


def quote_fixture(symbol: str, stamp: str, price: str = "123.450") -> bytes:
    fields = [""] * 31
    fields[3], fields[30] = price, stamp
    return f'v_{symbol}="{"~".join(fields)}";'.encode("gb18030")


def minute_fixture(stamp: str, volume: str = "100") -> bytes:
    return (
        'var _data=([{"day":"'
        + stamp
        + '","open":"10","high":"12",'
        + '"low":"9","close":"11.230","volume":"'
        + volume
        + '"}]);'
    ).encode()


@pytest.mark.parametrize(
    ("symbol", "code", "stamp", "expected_hour", "currency"),
    [
        ("600519.SS", "sh600519", "20261021161453", 8, "CNY"),
        ("0700.HK", "hk00700", "2026/10/21 16:08:21", 8, "HKD"),
        ("AAPL", "usAAPL", "2026-10-21 16:00:01", 20, "USD"),
    ],
)
def test_snapshot_time_currency_when_market_formats_differ(
    symbol: str, code: str, stamp: str, expected_hour: int, currency: str
) -> None:
    # Given: 受控供应商快照，时间是当地时间。
    raw = quote_fixture(code, stamp)
    # When: 边界解析。
    quote = parse_quote(raw, symbol, NOW)
    # Then: Decimal/UTC 保留，不能把更新时间变成可撮合时间。
    assert (quote.price, quote.market_time.hour, quote.market_time.tzinfo, quote.currency) == (
        Decimal("123.450"),
        expected_hour,
        UTC,
        currency,
    )
    assert quote.tradable is False


def test_us_dst_when_winter_quote_arrives() -> None:
    # Given: 美东冬令时收盘。
    raw = quote_fixture("usAAPL", "2026-12-01 16:00:01")
    # When: 解析当地时间。
    quote = parse_quote(raw, "AAPL", datetime(2026, 12, 1, 22, tzinfo=UTC))
    # Then: UTC 收盘为 21 点，不能全年固定为 20 点。
    assert quote.market_time.hour == 21


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity", "bad"])
def test_snapshot_rejected_when_price_invalid(price: str) -> None:
    # Given: 无效供应商价格。
    raw = quote_fixture("usAAPL", "2026-10-21 16:00:01", price)
    # When / Then: 不生成报价。
    with pytest.raises(MarketDataError):
        _ = parse_quote(raw, "AAPL", NOW)


def test_snapshot_rejected_when_symbol_mismatches() -> None:
    # Given: 上游响应了另一个证券。
    raw = quote_fixture("usSPY", "2026-10-21 16:00:01")
    # When / Then: 不把其价格赋予 AAPL。
    with pytest.raises(MarketDataError, match="invalid_response"):
        _ = parse_quote(raw, "AAPL", NOW)


def test_complete_bar_when_positive_volume() -> None:
    # Given: 已完成的五分钟正成交量区间。
    raw = minute_fixture("2026-10-21 14:55:00")
    # When: 解析到撮合候选。
    bars = parse_intraday(raw, "600519.SS", NOW)
    # Then: 时间严格覆盖完整的五分钟区间，收盘价保持精确。
    assert len(bars) == 1
    assert bars[0].interval_start is not None
    assert bars[0].interval_start == datetime(2026, 10, 21, 6, 50, tzinfo=UTC)
    assert bars[0].market_time == bars[0].interval_start + timedelta(minutes=5)
    assert (bars[0].price, bars[0].volume, bars[0].tradable) == (Decimal("11.230"), Decimal(100), True)


@pytest.mark.parametrize(("stamp", "volume"), [("2026-10-21 14:55:00", "0"), ("2026-10-22 14:55:00", "100")])
def test_bar_skipped_when_empty_or_incomplete(stamp: str, volume: str) -> None:
    # Given: 无成交区间或未来才完成的区间。
    raw = minute_fixture(stamp, volume)
    # When: 解析候选。
    bars = parse_intraday(raw, "600519.SS", NOW)
    # Then: 不能制造有效样本。
    assert bars == ()


def test_bar_rejected_when_interval_misaligned() -> None:
    # Given: 不能对应五分钟周期的时间标签。
    raw = minute_fixture("2026-10-21 14:56:00")
    # When / Then: 以明确错误拒绝。
    with pytest.raises(MarketDataError, match="invalid_bar_time"):
        _ = parse_intraday(raw, "600519.SS", NOW)


def test_history_uses_raw_close_when_adjusted_series_present() -> None:
    # Given: 同时存在原始日线与复权日线。
    raw = b'{"code":0,"data":{"sh600519":{"day":[["2026-10-21","10","11","12","9","100"]],'
    raw += b'"qfqday":[["2026-10-21","1","2","3","1","100"]]}}}'
    # When: 提取历史。
    bars = parse_history(raw, "600519.SS", NOW)
    # Then: 不把复权价格当成真实成交价格；日期只是日线标签。
    assert bars[0].close == Decimal(11)
    assert bars[0].market_time == datetime(2026, 10, 20, 16, tzinfo=UTC)


def test_history_rejected_when_only_adjusted_series_available() -> None:
    # Given: 上游没有未复权 day 数据。
    raw = b'{"code":0,"data":{"sh600519":{"qfqday":[["2026-10-21","1","2","3","1","100"]]}}}'
    # When / Then: 明确不可用，不切换到复权价。
    with pytest.raises(MarketDataError, match="history_unavailable"):
        _ = parse_history(raw, "600519.SS", NOW)


def test_fx_inverted_when_base_is_cny() -> None:
    # Given: 每人民币 1 元兑 0.2 USD 或 1.25 HKD。
    raw = b'[{"date":"2026-10-21","base":"CNY","quote":"USD","rate":0.2},'
    raw += b'{"date":"2026-10-21","base":"CNY","quote":"HKD","rate":1.25}]'
    # When: 转成每单位外币的人民币价格。
    rates = parse_fx(raw, NOW)
    # Then: 方向明确，人民币恒等值不冒充外部价格。
    assert {rate.currency: rate.cny_per_unit for rate in rates} == {
        "CNY": Decimal(1),
        "USD": Decimal(5),
        "HKD": Decimal("0.8"),
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'[{"date":"2026-10-21","base":"CNY","quote":"USD","rate":0}]',
        b'[{"date":"2026-10-21","base":"USD","quote":"USD","rate":1}]',
        b'[{"date":"2026-10-21","base":"CNY","quote":"USD","rate":0.2}]',
        b'[{"date":"2026-10-22","base":"CNY","quote":"USD","rate":0.2},{"date":"2026-10-22","base":"CNY","quote":"HKD","rate":1.25}]',
    ],
)
def test_fx_rejected_when_incomplete_or_invalid(raw: bytes) -> None:
    # Given: 缺失、零值、币种方向错误或未来日期。
    # When / Then: 不伪造固定汇率。
    with pytest.raises(MarketDataError):
        _ = parse_fx(raw, NOW)


@final
class TransportFixture(MarketDataClient):
    """仅替代外部网络边界；缓存、并发锁与实际解析均运行真实实现。"""

    def __init__(self, raw: bytes) -> None:
        super().__init__()
        self.raw = raw
        self.calls = 0
        self.failure = False
        self.block = False
        self.entered = Event()
        self.release = Event()

    @override
    def _get(self, url: str) -> bytes:
        self.calls += 1
        self.entered.set()
        if self.block:
            assert self.release.wait(timeout=3)
        if self.failure:
            raise MarketDataError("http_error", "429")
        return self.raw


def test_concurrent_quotes_share_one_request_and_original_age() -> None:
    # Given: 网络响应先阻塞，八个并发调用进入同一客户端。
    client = TransportFixture(quote_fixture("usAAPL", "2026-10-21 16:00:01"))
    client.block = True
    # When: 同时读取同一证券，并允许第一笔网络响应返回。
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = [pool.submit(client.quote, "AAPL") for _ in range(8)]
        assert client.entered.wait(timeout=3)
        client.release.set()
        quotes = [future.result(timeout=3) for future in pending]
    # Then: 只发出一次真实实现所依赖的网络请求，所有读取保留同一取得时间。
    assert client.calls == 1
    assert len({quote.retrieved_at for quote in quotes}) == 1
    assert all(quote.market_time == quotes[0].market_time for quote in quotes)


def test_quote_refreshes_only_after_cache_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 首次取得一笔带原始来源时间的报价。
    clock = [0.0]
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    client = TransportFixture(quote_fixture("usAAPL", "2026-10-21 16:00:01"))
    initial = client.quote("AAPL")
    # When: 在有效期内和边界处读取。
    clock[0] = 59
    cached = client.quote("AAPL")
    clock[0] = 60
    refreshed = client.quote("AAPL")
    # Then: 有效期内不伪造取得时间，过期才重取；供应商时间仍未变新。
    assert client.calls == 2
    assert cached.retrieved_at == initial.retrieved_at
    assert refreshed.market_time == initial.market_time


def test_failure_is_throttled_then_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 来源暂时返回 429。
    clock = [0.0]
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    client = TransportFixture(quote_fixture("usAAPL", "2026-10-21 16:00:01"))
    client.failure = True
    # When: 同一失败重复读取，十五秒后来源恢复。
    for _ in range(2):
        with pytest.raises(MarketDataError, match="http_error"):
            _ = client.quote("AAPL")
    client.failure = False
    clock[0] = 15
    quote = client.quote("AAPL")
    # Then: 失败期间只请求一次，后续能明确重试成功。
    assert client.calls == 2
    assert quote.price == Decimal("123.450")


def test_daily_fx_cache_outlasts_quote_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 日频外汇返回已知日期，不需要跟随每分钟行情重复读取。
    clock = [0.0]
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    raw = b'[{"date":"2026-10-21","base":"CNY","quote":"USD","rate":0.2},'
    raw += b'{"date":"2026-10-21","base":"CNY","quote":"HKD","rate":1.25}]'
    client = TransportFixture(raw)
    initial = client.fx_rates()
    # When: 一小时内重复读取。
    clock[0] = 3599
    cached = client.fx_rates()
    # Then: 来源日期和取得时间均保持不变，只有一次请求。
    assert client.calls == 1
    assert cached == initial
