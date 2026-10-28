"""受控三市场实际解析入口：观察版本边界与既有拒绝条件。"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never, final, override

import pytest

from app.engine import Engine
from app.marketdata_futu_parser import parse_futu_intraday
from app.marketdata_models import MarketDataError, Quote
from app.marketdata_parsers import parse_intraday
from app.marketdata_us import parse_us_intraday
from app.models import DomainError, OrderInput
from app.storage import Store
from tests.test_engine import ControlledProvider
from tests.test_marketdata import TransportFixture

if TYPE_CHECKING:
    from pydantic import JsonValue

Market = Literal["CN", "US", "HK-start", "HK-end"]
MARKETS: tuple[Market, ...] = ("CN", "US", "HK-start", "HK-end")


def parse_case(market: Market, raw: bytes, elapsed: int) -> tuple[Quote, ...]:
    end = datetime(2026, 10, 27, 1, 35, tzinfo=UTC)
    match market:
        case "CN":
            return parse_intraday(raw, "600519.SS", end + timedelta(seconds=elapsed))
        case "US":
            return parse_us_intraday(raw, "AAPL", end.replace(hour=13) + timedelta(seconds=elapsed))
        case "HK-start" | "HK-end":
            return parse_futu_intraday(raw, "0700.HK", end + timedelta(seconds=elapsed))
        case _:
            assert_never(market)


def invalid_values(market: Market, mutation: str) -> tuple[tuple[str, str], ...]:
    price_key = {"CN": "close", "US": "c", "HK-start": "closePrice", "HK-end": "closePrice"}[market]
    volume_key = "v" if market == "US" else "volume"
    return {
        "nan-price": ((price_key, "NaN"),),
        "infinite-price": ((price_key, "Infinity"),),
        "negative-price": ((price_key, "-1"),),
        "nan-volume": ((volume_key, "NaN"),),
        "infinite-volume": ((volume_key, "Infinity"),),
        "negative-volume": ((volume_key, "-1"),),
    }.get(mutation, ())


def response(market: Market, *, successor: bool = False, mutation: str = "") -> bytes:
    rows: list[dict[str, JsonValue]] = []
    for minute in [40, 35] if successor else [35]:
        stamp = datetime(2026, 10, 27, 9, minute, tzinfo=UTC).replace(tzinfo=None)
        if market == "HK-start":
            stamp -= timedelta(minutes=5)
        stamp += timedelta(
            minutes=int(mutation == "misaligned"),
            seconds=int(mutation == "seconds"),
            microseconds=int(mutation == "microsecond"),
        )
        stamp = stamp.replace(tzinfo=UTC if mutation == "timezone" else None)
        volume = "0" if mutation == "zero" else "100"
        high = "10" if mutation == "ohlc" else "12"
        match market:
            case "CN":
                row: dict[str, JsonValue] = {
                    "day": str(stamp),
                    "open": "10",
                    "high": high,
                    "low": "9",
                    "close": "11",
                    "volume": volume,
                }
            case "US":
                row = {"d": str(stamp), "o": "10", "h": high, "l": "9", "c": "11", "v": volume}
            case "HK-start" | "HK-end":
                row = {
                    "time": str(stamp),
                    "isBlank": False,
                    "openPrice": "10",
                    "highPrice": high,
                    "lowPrice": "9",
                    "closePrice": "11",
                    "volume": volume,
                }
            case _:
                assert_never(market)
        row.update(invalid_values(market, mutation))
        rows.append(row)
    if mutation in {"duplicate", "duplicate-zero", "duplicate-blank", "duplicate-conflict"}:
        duplicate = rows[-1].copy()
        if mutation == "duplicate-zero":
            duplicate["v" if market == "US" else "volume"] = "0"
        duplicate.update({"isBlank": True} if mutation == "duplicate-blank" else {})
        price_key = {"CN": "close", "US": "c", "HK-start": "closePrice", "HK-end": "closePrice"}[market]
        duplicate.update({price_key: "10"} if mutation == "duplicate-conflict" else {})
        rows.append(duplicate)
    if mutation == "malformed-successor":
        rows.insert(0, {"time": "invalid", "day": "invalid", "d": "invalid"})
    if market in {"CN", "US"}:
        return f"var _data=({json.dumps(rows)});".encode()
    return json.dumps({"retType": 0, "s2c": {"security": {"market": 1, "code": "00700"}, "klList": rows}}).encode()


@pytest.fixture(autouse=True)
def explicit_hk_label(monkeypatch: pytest.MonkeyPatch, market: Market) -> None:
    monkeypatch.setenv("STOCKMOCK_FUTU_BAR_LABEL", "start" if market == "HK-start" else "end")


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("elapsed", [-300, -1])
def test_latest_interval_is_excluded_before_end(market: Market, elapsed: int) -> None:
    # Given / When / Then: 原始取得时尚未结束的区间不能用于撮合。
    assert parse_case(market, response(market), elapsed) == ()


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("offset", [0, 1, 86400])
def test_last_interval_without_successor_is_available_at_end(market: Market, offset: int) -> None:
    # Given / When: 恰好结束或之后；历史区间不在解析层因新鲜度被丢弃。
    quotes = parse_case(market, response(market), offset)
    # Then: 保留市场时间、价格、量及可撮合语义。
    assert len(quotes) == 1
    assert quotes[0].tradable
    identity = {
        "CN": ("600519.SS", "CNY", "sina-public-5m"),
        "US": ("AAPL", "USD", "sina-us-5m"),
        "HK-start": ("0700.HK", "HKD", "futu-hk-5m-raw"),
        "HK-end": ("0700.HK", "HKD", "futu-hk-5m-raw"),
    }[market]
    assert (quotes[0].symbol, quotes[0].currency, quotes[0].source) == identity
    assert quotes[0].retrieved_at == quotes[0].market_time + timedelta(seconds=offset)
    assert quotes[0].price == 11
    assert quotes[0].volume == 100
    assert quotes[0].interval_start == quotes[0].market_time - timedelta(minutes=5)


@pytest.mark.parametrize("market", MARKETS)
def test_unsorted_future_successor_does_not_hide_ended_interval(market: Market) -> None:
    # Given: 更晚的区间排在响应前面，标签还在未来。
    quotes = parse_case(market, response(market, successor=True), 1)
    # Then: 只输出已经结束的前根，不接受未来后继。
    assert len(quotes) == 1
    assert quotes[0].market_time.minute == 35


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("elapsed", [-1, -300])
def test_future_label_remains_excluded_with_successor(market: Market, elapsed: int) -> None:
    assert parse_case(market, response(market, successor=True), elapsed) == ()


@pytest.mark.parametrize("market", MARKETS)
def test_zero_volume_remains_excluded_after_end(market: Market) -> None:
    assert parse_case(market, response(market, mutation="zero"), 86400) == ()


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize(
    "mutation",
    [
        "misaligned",
        "microsecond",
        "seconds",
        "timezone",
        "ohlc",
        "duplicate",
        "duplicate-zero",
        "duplicate-conflict",
        "malformed-successor",
        "nan-price",
        "infinite-price",
        "negative-price",
        "nan-volume",
        "infinite-volume",
        "negative-volume",
    ],
)
def test_invalid_response_cannot_confirm_previous_interval(market: Market, mutation: str) -> None:
    # Given: 同一响应中包含畸形、非五分钟、重复或 OHLC 不一致的数据。
    # When / Then: 拒绝整批，不能因观察版本筛选而绕过既有校验。
    with pytest.raises(MarketDataError):
        _ = parse_case(market, response(market, mutation=mutation), 1)


@final
@dataclass(frozen=True, slots=True)
class ObservedProvider(ControlledProvider):
    """仅替代HTTP，分钟线仍走实际解析器和Engine.prepare。"""

    retrieved_at: datetime = datetime(2026, 10, 27, 13, 52, tzinfo=UTC)

    @override
    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        return parse_us_intraday(response("US"), symbol, self.retrieved_at)


@pytest.mark.parametrize("market", ["US"])
@pytest.mark.parametrize(
    ("elapsed", "allowed", "latest_minute"),
    [
        (0, True, 35),
        (1, True, 35),
        (1020, True, 35),
        (1200, True, 35),
        (1201, False, 35),
        (1319, False, 35),
        (1320, False, 35),
    ],
)
def test_engine_prepare_preserves_twenty_minute_protection(
    market: Market, elapsed: int, latest_minute: int, tmp_path: Path, *, allowed: bool
) -> None:
    assert market == "US"
    # Given: 受控单区间经真实解析入口，年龄覆盖刚结束、20分钟边界及过时。
    now = datetime(2026, 10, 27, 13, 35, tzinfo=UTC) + timedelta(seconds=elapsed)
    provider = ObservedProvider(retrieved_at=now)
    engine = Engine(Store(tmp_path / "isolated.sqlite3"), provider)
    request = OrderInput(
        request_id="observation-phase",
        symbol="AAPL",
        side="buy",
        order_type="limit",
        quantity=1,
        limit_price=Decimal(11),
    )
    assert provider.intraday("AAPL")[-1].market_time.minute == latest_minute
    # When / Then: 实际入口在20分钟边界内接受，超过则沿用明确quote_stale。
    if allowed:
        quote, _, _ = engine.prepare(request, now)
        assert quote.market_time.minute == latest_minute
    else:
        with pytest.raises(DomainError, match="行情超过容忍时效") as error:
            _ = engine.prepare(request, now)
        assert error.value.code == "quote_stale"
    assert not engine.store.read().orders
    assert not engine.store.read().ledger


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("mutation", ["duplicate", "duplicate-zero", "duplicate-conflict"])
def test_duplicate_labels_rejected_even_when_future(market: Market, mutation: str) -> None:
    with pytest.raises(MarketDataError):
        _ = parse_case(market, response(market, mutation=mutation), -1)


@pytest.mark.parametrize("market", ["HK-start", "HK-end"])
def test_blank_duplicate_still_rejects_whole_futu_response(market: Market) -> None:
    with pytest.raises(MarketDataError, match="duplicate_bar"):
        _ = parse_case(market, response(market, mutation="duplicate-blank"), 1)


@pytest.mark.parametrize("market", MARKETS)
def test_intraday_cache_keeps_original_receipt_across_interval_end(
    market: Market, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 在区间结束前一秒取得响应，使用实际下载缓存和解析入口。
    end = datetime(2026, 10, 27, 13 if market == "US" else 1, 40, tzinfo=UTC)
    clock = [0.0]
    wall = [end - timedelta(seconds=1)]

    class Clock(datetime):
        @classmethod
        @override
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return wall[0]

    monkeypatch.setattr("app.marketdata.datetime", Clock)
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    client = TransportFixture(response(market, successor=True))
    symbol = {"CN": "600519.SS", "US": "AAPL", "HK-start": "0700.HK", "HK-end": "0700.HK"}[market]
    initial = client.intraday(symbol)
    assert len(initial) == 1
    assert initial[0].market_time == end - timedelta(minutes=5)
    # When: 墙钟跨过结束，但60秒缓存未到期。
    clock[0], wall[0] = 59, end + timedelta(seconds=58)
    cached = client.intraday(symbol)
    assert cached == initial
    assert client.calls == 1
    assert cached[0].retrieved_at == end - timedelta(seconds=1)
    # Then: 只有真正重取才能用新的取得时间接纳该区间。
    clock[0], wall[0] = 60, end + timedelta(seconds=59)
    refreshed = client.intraday(symbol)
    assert client.calls == 2
    assert len(refreshed) == 2
    assert refreshed[-1].market_time == end
    assert all(quote.retrieved_at == wall[0] for quote in refreshed)


@pytest.mark.parametrize("market", MARKETS)
def test_response_identity_mismatch_is_rejected(market: Market) -> None:
    now = datetime(2026, 10, 27, 15, tzinfo=UTC)
    match market:
        case "CN":
            parser, raw, symbol = parse_intraday, response(market), "AAPL"
        case "US":
            parser, raw, symbol = parse_us_intraday, response(market), "600519.SS"
        case "HK-start" | "HK-end":
            parser, raw, symbol = parse_futu_intraday, response(market).replace(b"00700", b"00005"), "0700.HK"
        case _:
            assert_never(market)
    with pytest.raises(MarketDataError):
        _ = parser(raw, symbol, now)
