"""受控三市场实际解析入口：定稿余量与既有拒绝条件。"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


def response(market: Market, *, successor: bool = False, mutation: str = "") -> bytes:
    rows: list[dict[str, JsonValue]] = []
    for minute in [40, 35] if successor else [35]:
        stamp = datetime(2026, 10, 27, 9, minute, tzinfo=UTC).replace(tzinfo=None)
        if market == "HK-start":
            stamp -= timedelta(minutes=5)
        if mutation == "misaligned":
            stamp += timedelta(minutes=1)
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
        rows.append(row)
    if mutation == "duplicate":
        rows.append(rows[-1].copy())
    if mutation == "malformed-successor":
        rows.insert(0, {"time": "invalid", "day": "invalid", "d": "invalid"})
    if market in {"CN", "US"}:
        return f"var _data=({json.dumps(rows)});".encode()
    return json.dumps({"retType": 0, "s2c": {"security": {"market": 1, "code": "00700"}, "klList": rows}}).encode()


@pytest.fixture(autouse=True)
def explicit_hk_label(monkeypatch: pytest.MonkeyPatch, market: Market) -> None:
    monkeypatch.setenv("STOCKMOCK_FUTU_BAR_LABEL", "start" if market == "HK-start" else "end")


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("offset", [-89, -1])
def test_latest_interval_waits_for_settle(market: Market, offset: int) -> None:
    # Given: 最新区间刚结束，响应内没有后继。
    raw = response(market)
    # When / Then: 不把尚在修订的末根输出为可撮合样本。
    assert parse_case(market, raw, (1020 if market == "US" else 90) + offset) == ()


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("offset", [0, 1, 86400])
def test_last_interval_without_successor_is_available_after_settle(market: Market, offset: int) -> None:
    # Given / When: 等于或超过余量；旧区间不在解析层因新鲜度被丢弃。
    quotes = parse_case(market, response(market), (1020 if market == "US" else 90) + offset)
    # Then: 保留市场时间、价格、量及可撮合语义。
    assert len(quotes) == 1
    assert quotes[0].tradable
    assert quotes[0].price == 11
    assert quotes[0].volume == 100
    assert quotes[0].interval_start == quotes[0].market_time - timedelta(minutes=5)


@pytest.mark.parametrize("market", MARKETS)
def test_unsorted_successor_cannot_bypass_settle(market: Market) -> None:
    # Given: 更晚的区间排在响应前面，标签还在未来。
    quotes = parse_case(market, response(market, successor=True), 1)
    # Then: 后继不能使不足余量的前根提前输出。
    assert quotes == ()


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("elapsed", [-1, -300])
def test_future_label_remains_excluded_with_successor(market: Market, elapsed: int) -> None:
    assert parse_case(market, response(market, successor=True), elapsed) == ()


@pytest.mark.parametrize("market", MARKETS)
def test_zero_volume_remains_excluded_after_settle(market: Market) -> None:
    assert parse_case(market, response(market, mutation="zero"), 86400) == ()


@pytest.mark.parametrize("market", MARKETS)
@pytest.mark.parametrize("mutation", ["misaligned", "ohlc", "duplicate", "malformed-successor"])
def test_invalid_response_cannot_confirm_previous_interval(market: Market, mutation: str) -> None:
    # Given: 同一响应中包含畸形、非五分钟、重复或 OHLC 不一致的数据。
    # When / Then: 拒绝整批，不能因新定稿筛选而绕过既有校验。
    with pytest.raises(MarketDataError):
        _ = parse_case(market, response(market, mutation=mutation), 1)


@final
@dataclass(frozen=True, slots=True)
class SettledProvider(ControlledProvider):
    """仅替代HTTP，分钟线仍走实际解析器和Engine.prepare。"""

    retrieved_at: datetime = datetime(2026, 10, 27, 13, 52, tzinfo=UTC)

    @override
    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        return parse_us_intraday(response("US", successor=True), symbol, self.retrieved_at)


@pytest.mark.parametrize("market", ["US"])
@pytest.mark.parametrize(
    ("elapsed", "allowed", "latest_minute"),
    [(1020, True, 35), (1200, True, 35), (1201, False, 35), (1319, False, 35), (1320, True, 40)],
)
def test_engine_prepare_preserves_twenty_minute_protection(
    market: Market, elapsed: int, latest_minute: int, tmp_path: Path, *, allowed: bool
) -> None:
    assert market == "US"
    # Given: 两个连续真实格式区间，最新合格样本年龄在17至不足22分钟循环。
    now = datetime(2026, 10, 27, 13, 35, tzinfo=UTC) + timedelta(seconds=elapsed)
    provider = SettledProvider(retrieved_at=now)
    engine = Engine(Store(tmp_path / "isolated.sqlite3"), provider)
    request = OrderInput(
        request_id="settle-phase", symbol="AAPL", side="buy", order_type="limit", quantity=1, limit_price=Decimal(11)
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
