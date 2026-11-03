"""0.5.0 展示衍生计算：受控时刻下的日线定稿、涨跌状态与持仓容错，全部为受控测试数据。"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import override

import pytest

from app import views
from app.activated_registry import RuleProvider
from app.engine import Engine
from app.marketdata import MarketDataClient, MarketDataError, Quote
from app.marketdata_reference import QuoteReference
from app.models import DomainError, Lot, WorkerView
from app.storage import Store
from app.views import Change, bar_complete, portfolio, quote_change
from tests.test_engine import ControlledProvider
from tests.test_marketdata_reference import quote_raw

FORMAL_DISABLED = views.CHANGE_DISABLED_MARKETS


@pytest.fixture(autouse=True)
def verified_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    """受控测试默认模拟核对已通过；正式常量在模块加载时另存并单独断言。"""
    monkeypatch.setattr(views, "CHANGE_DISABLED_MARKETS", frozenset[views.Market]())


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


BUFFER = timedelta(minutes=10)


@pytest.mark.parametrize(
    ("market", "day", "retrieved", "buffer", "expected"),
    [
        ("CN", date(2026, 10, 29), "2026-10-30T10:00:00+08:00", BUFFER, True),
        ("CN", date(2026, 10, 30), "2026-10-30T10:00:00+08:00", BUFFER, False),
        ("CN", date(2026, 10, 30), "2026-10-30T15:05:00+08:00", BUFFER, False),
        ("CN", date(2026, 10, 30), "2026-10-30T15:10:00+08:00", BUFFER, True),
        ("HK", date(2026, 12, 24), "2026-12-24T12:15:00+08:00", BUFFER, True),
        ("HK", date(2026, 12, 24), "2026-12-24T12:05:00+08:00", BUFFER, False),
        ("US", date(2026, 11, 27), "2026-11-27T13:00:00-05:00", timedelta(0), True),
        ("US", date(2026, 11, 27), "2026-11-27T12:59:00-05:00", timedelta(0), False),
        ("CN", date(2026, 10, 31), "2026-10-31T20:00:00+08:00", BUFFER, False),
        ("CN", date(2027, 1, 15), "2027-01-15T20:00:00+08:00", BUFFER, None),
        ("CN", date(2026, 10, 30), "2026-10-30T20:00:00+08:00", None, False),
        ("US", date(2026, 10, 30), "2026-10-30T20:00:00-04:00", None, False),
        ("CN", date(2026, 10, 29), "2026-10-30T20:00:00+08:00", None, True),
    ],
    ids=[
        "previous_day",
        "same_day_intraday",
        "after_close_within_buffer",
        "after_buffer",
        "hk_half_day_after_buffer",
        "hk_half_day_within_buffer",
        "us_early_close",
        "us_before_early_close",
        "non_session_day",
        "calendar_out_of_bounds",
        "transitional_cn",
        "transitional_us",
        "transitional_previous_day",
    ],
)
def test_bar_complete_uses_retrieval_time(
    market: views.Market, day: date, retrieved: str, buffer: timedelta | None, *, expected: bool | None
) -> None:
    assert bar_complete(market, day, at(retrieved), buffer) is expected


def test_cached_intraday_bar_stays_incomplete_after_close() -> None:
    # 收盘前取得、收盘后从缓存读出：判定只看取得时刻，与读取时刻无关。
    assert bar_complete("CN", date(2026, 10, 30), at("2026-10-30T14:30:00+08:00"), timedelta(0)) is False


def test_transitional_buffers_are_all_none() -> None:
    assert views.CLOSE_BUFFER == {"CN": None, "HK": None, "US": None}


def reference(
    stamp: str, price: str = "101", previous: str | None = "100", symbol: str = "600519.SS"
) -> QuoteReference:
    time = at(stamp)
    quote = Quote(symbol, Decimal(price), "CNY", time, time, "controlled-test")
    return QuoteReference(quote, None if previous is None else Decimal(previous))


def test_available_change_uses_same_response_previous_close() -> None:
    change = quote_change("CN", reference("2026-10-30T15:00:00+08:00", "101.5", "100"))
    assert change == Change(
        Decimal(100), Decimal("1.5"), Decimal("1.5"), date(2026, 10, 30), date(2026, 10, 29), "available"
    )


def test_pre_open_uses_quote_market_day_session_open() -> None:
    change = quote_change("CN", reference("2026-11-02T09:25:00+08:00"))
    assert (change.change_status, change.change, change.change_day) == ("pre_open", None, date(2026, 11, 2))


def test_previous_day_quote_before_open_shows_dated_change() -> None:
    change = quote_change("CN", reference("2026-10-30T15:00:00+08:00"))
    assert (change.change_status, change.change_day) == ("available", date(2026, 10, 30))


def test_missing_previous_close_status() -> None:
    change = quote_change("CN", reference("2026-10-30T15:00:00+08:00", previous=None))
    assert (change.change_status, change.change, change.change_base_day) == (
        "no_previous_close",
        None,
        date(2026, 10, 29),
    )


def test_disabled_market_hides_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(views, "CHANGE_DISABLED_MARKETS", frozenset({"CN"}))
    assert quote_change("CN", reference("2026-10-30T15:00:00+08:00")) == Change(change_status="market_disabled")


def test_calendar_out_of_bounds_and_non_session_quote_are_unknown() -> None:
    assert quote_change("CN", reference("2027-01-15T10:00:00+08:00")).change_status == "unknown"
    assert quote_change("CN", reference("2026-10-31T10:00:00+08:00")).change_status == "unknown"


def test_derived_domain_error_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_market: str) -> object:
        raise DomainError("calendar_unavailable", "受控失败")

    monkeypatch.setattr(views, "calendar", broken)
    assert quote_change("CN", reference("2026-10-30T15:00:00+08:00")).change_status == "unknown"


@pytest.mark.parametrize(
    ("stamp", "day", "base"),
    [
        ("2026-10-30T11:00:00-04:00", date(2026, 10, 30), date(2026, 10, 29)),
        ("2026-10-31T03:30:00+08:00", date(2026, 10, 30), date(2026, 10, 29)),
        ("2026-12-01T10:00:00-05:00", date(2026, 12, 1), date(2026, 11, 30)),
        ("2026-12-01T23:00:00+08:00", date(2026, 12, 1), date(2026, 11, 30)),
    ],
    ids=["edt_day", "edt_beijing_early_morning", "est_day", "est_beijing_night"],
)
def test_us_change_day_uses_new_york_date(stamp: str, day: date, base: date) -> None:
    change = quote_change("US", reference(stamp, symbol="AAPL"))
    assert (change.change_status, change.change_day, change.change_base_day) == ("available", day, base)


QUOTE_STAMP = "20261030150000"
NOW = at("2026-10-30T20:00:00+08:00")


@dataclass
class FakeClient(MarketDataClient):
    """替换网络边界；可令昨收报价与估值报价不同或报价失败。"""

    body: bytes = field(default_factory=lambda: quote_raw("sh600519", QUOTE_STAMP, price="100", previous="98"))
    failure: Exception | None = None
    calls: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        MarketDataClient.__init__(self)

    @override
    def _get(self, url: str) -> bytes:
        self.calls.append(url)
        if self.failure is not None:
            raise self.failure
        return self.body


def seeded(tmp_path: Path, quote_time: datetime) -> Engine:
    engine = Engine(Store(tmp_path / "ledger.sqlite3"), ControlledProvider(quote_time=quote_time))
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="600519.SS", quantity=100, cost=Decimal(9000), sellable_at=NOW))
        engine.store.save(connection, state)
    return engine


def test_portfolio_change_available_when_same_quote(tmp_path: Path) -> None:
    engine = seeded(tmp_path, at("2026-10-30T15:00:00+08:00"))
    display = RuleProvider(engine.registry, FakeClient())
    row = portfolio(engine, NOW, WorkerView(), display).positions[0]
    assert (row.change_status, row.previous_close, row.change, row.change_day) == (
        "available",
        Decimal(98),
        Decimal(2),
        date(2026, 10, 30),
    )


def test_portfolio_quote_changed_when_time_or_price_differs(tmp_path: Path) -> None:
    engine = seeded(tmp_path, at("2026-10-30T14:55:00+08:00"))
    display = RuleProvider(engine.registry, FakeClient())
    assert portfolio(engine, NOW, WorkerView(), display).positions[0].change_status == "quote_changed"
    other = seeded(tmp_path / "price", at("2026-10-30T15:00:00+08:00"))
    body = quote_raw("sh600519", QUOTE_STAMP, price="100.01", previous="98")
    display = RuleProvider(other.registry, FakeClient(body=body))
    assert portfolio(other, NOW, WorkerView(), display).positions[0].change_status == "quote_changed"


@pytest.mark.parametrize(
    "failure",
    [MarketDataError("http_error", "503"), ValueError("x"), IndexError("x"), DomainError("x", "y")],
)
def test_portfolio_tolerates_reference_failure(tmp_path: Path, failure: Exception) -> None:
    engine = seeded(tmp_path, at("2026-10-30T15:00:00+08:00"))
    baseline = portfolio(engine, NOW, WorkerView())
    display = RuleProvider(engine.registry, FakeClient(failure=failure))
    result = portfolio(engine, NOW, WorkerView(), display)
    assert result.positions[0].change_status == "unknown"
    assert (result.total_assets_cny, result.valuation_status, result.warnings) == (
        baseline.total_assets_cny,
        baseline.valuation_status,
        baseline.warnings,
    )
    assert baseline.positions[0].change_status is None


def test_stale_position_does_not_request_reference(tmp_path: Path) -> None:
    engine = seeded(tmp_path, at("2026-10-29T10:00:00+08:00"))
    client = FakeClient()
    row = portfolio(engine, NOW, WorkerView(), RuleProvider(engine.registry, client)).positions[0]
    assert (row.market_price, row.change_status, client.calls) == (None, "no_price", [])


def test_all_markets_enabled_after_previous_close_verification() -> None:
    assert frozenset() == FORMAL_DISABLED
