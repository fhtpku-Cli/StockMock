"""按 HKEX CT/077/26 与 NYSE 2026–2028 官方表独立核对全年。"""

from datetime import UTC, date, datetime, time, timedelta
from importlib import import_module
from typing import Final, Literal
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pytest

from app.calendar import calendar, is_open, window
from app.catalog import instrument
from app.market_session import project
from tests.market_session_support import receipt

# HKEX: https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2026/ce_SEHK_CT_077_2026.pdf
# NYSE: https://www.nyse.com/trade/hours-calendars
CLOSED: Final = {
    "HK": frozenset(
        {
            "01-01",
            "02-08",
            "02-09",
            "03-26",
            "03-29",
            "04-05",
            "05-13",
            "06-09",
            "07-01",
            "09-16",
            "10-01",
            "10-08",
            "12-27",
        }
    ),
    "US": frozenset({"01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"}),
}
HALF: Final = {"HK": frozenset({"02-05", "12-24", "12-31"}), "US": frozenset({"11-26"})}
SYMBOL: Final = {"HK": "0700.HK", "US": "AAPL"}
ZONE: Final = {"HK": ZoneInfo("Asia/Hong_Kong"), "US": ZoneInfo("America/New_York")}


@pytest.mark.parametrize("market", ["HK", "US"])
@pytest.mark.parametrize("offset", range(365))
def test_official_day_when_each_2027_date(market: Literal["HK", "US"], offset: int) -> None:
    # Given: 休市/半日日期直接抄自官方表，不以依赖输出生成预期。
    day = date(2027, 1, 1) + timedelta(days=offset)
    local = datetime.combine(day, time(10), ZONE[market])
    closed = day.weekday() >= 5 or day.strftime("%m-%d") in CLOSED[market]
    half = day.strftime("%m-%d") in HALF[market]
    close_hour = ({"HK": 12, "US": 13}[market]) if half else 16
    # When: 查询真实共享日历和产品时段投影。
    view = project(market, local)
    # Then: 每个日期开休市一致，交易日开收盘与午休逐一对应官方时段。
    assert view.phase == ("non_session_day" if closed else "supported")
    if closed:
        assert not is_open(instrument(SYMBOL[market]), local)
        return
    opening, closing = window(instrument(SYMBOL[market]), local)
    assert opening == datetime.combine(day, time(9, 30), ZONE[market])
    assert closing == datetime.combine(day, time(close_hour), ZONE[market])
    lunch = project(market, local.replace(hour=12, minute=30))
    afternoon = project(market, local.replace(hour=13, minute=30))
    assert lunch.phase == (
        {"HK": "after_hours", "US": "supported"}[market] if half else {"HK": "break", "US": "supported"}[market]
    )
    assert afternoon.phase == ("after_hours" if half else "supported")
    assert view.current_period is not None
    assert view.current_period.end == (local.replace(hour=12, minute=0) if market == "HK" else closing)
    if market == "HK" and not half:
        assert lunch.next_period is not None
        assert lunch.next_period.start == local.replace(hour=13, minute=0)
        assert afternoon.current_period is not None
        assert afternoon.current_period.end == closing


@pytest.mark.parametrize(
    ("market", "boundary"),
    [
        ("HK", ("2027-02-05", "2027-02-10", 12)),
        ("HK", ("2027-12-24", "2027-12-28", 12)),
        ("HK", ("2027-12-31", "2028-01-03", 12)),
        ("US", ("2027-11-26", "2027-11-29", 13)),
        ("US", ("2027-12-31", "2028-01-03", 16)),
    ],
)
def test_next_session_when_close_reached(market: Literal["HK", "US"], boundary: tuple[str, str, int]) -> None:
    # Given: 官方半日截止，另含美股正常年末收盘。
    day, next_day, hour = boundary
    close = datetime.combine(date.fromisoformat(day), time(hour), ZONE[market])
    item = instrument(SYMBOL[market])
    # When: 分别查询截止前一微秒、等号与下一支持段。
    before = project(market, close - timedelta(microseconds=1))
    after = project(market, close)
    # Then: 左闭右开，半日后不误报午休，下一开盘跨假日/跨年正确。
    assert before.phase == "supported"
    assert is_open(item, close - timedelta(microseconds=1))
    assert not is_open(item, close)
    assert after.phase == "after_hours"
    expected = datetime.combine(date.fromisoformat(next_day), time(9, 30), ZONE[market])
    assert after.next_period is not None
    assert after.next_period.start == expected
    assert window(item, close)[0] == expected
    receipt(f"implementation-boundary-{market}-{day}.json", after.model_dump_json(indent=2))


@pytest.mark.parametrize(
    ("day", "hour"), [("2027-03-12", 14), ("2027-03-15", 13), ("2027-11-05", 13), ("2027-11-08", 14)]
)
def test_utc_open_when_us_dst_changes(day: str, hour: int) -> None:
    # Given: 美东夏令时切换前后的交易日。
    local = datetime.combine(date.fromisoformat(day), time(10), ZONE["US"])
    # When: 查询实际开市时刻。
    opening, _ = window(instrument("AAPL"), local)
    # Then: 美东固定 9:30，UTC 随夏令时改变。
    assert opening == datetime.combine(local.date(), time(hour, 30), UTC)


@pytest.mark.parametrize(("market", "name"), [("HK", "XHKG"), ("US", "XNYS")])
def test_later_default_when_dependency_rolls_forward(
    monkeypatch: pytest.MonkeyPatch, market: Literal["HK", "US"], name: str
) -> None:
    # Given: 控制依赖的默认日期，实例仍由真实依赖生成；显式范围先使其工厂缓存失配。
    future = xcals.get_calendar(name, end="2028-12-29").last_session
    monkeypatch.setattr(import_module("exchange_calendars.exchange_calendar"), "GLOBAL_DEFAULT_END", future)
    calendar.cache_clear()
    try:
        # When: 共享入口在未来默认已超过最低覆盖日期时重新取得实例。
        cal = calendar(market)
        # Then: 年末以后真实交易日仍可访问，没有缩回 2028/1/3。
        assert cal.last_session.to_pydatetime().date() == date(2028, 12, 29)
        assert cal.date_to_session("2028-12-29", direction="none").to_pydatetime().date() == date(2028, 12, 29)
        receipt(f"implementation-future-default-{market}.txt", cal.last_session.to_pydatetime().isoformat())
    finally:
        calendar.cache_clear()
        _ = xcals.get_calendar(name, end="2028-01-03")
