"""按国办发明电〔2026〕10号独立核对 A 股 2027 全年开休市；上交所年度通知发布后须再逐日比对。"""

from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import pytest
from exchange_calendars.exchange_calendar_xshg import XSHGExchangeCalendar

from app.calendar import calendar, is_open, sellable_time, window
from app.catalog import instrument
from app.models import DomainError

# 国务院放假区间抄自原文：https://www.gov.cn/zhengce/zhengceku/202611/content_7085102.htm
# 预期由区间推导，不以产品常量生成：区间内周一至周五休市，调休上班的周末不开市。
OFFICIAL_RANGES: Final = (
    ("2027-01-01", "2027-01-03"),
    ("2027-02-05", "2027-02-13"),
    ("2027-04-03", "2027-04-05"),
    ("2027-05-01", "2027-05-05"),
    ("2027-06-09", "2027-06-09"),
    ("2027-09-15", "2027-09-15"),
    ("2027-10-01", "2027-10-07"),
)
MAKEUP_WEEKENDS: Final = ("2027-01-31", "2027-02-14", "2027-05-08", "2027-09-26", "2027-10-09")
SHANGHAI: Final = ZoneInfo("Asia/Shanghai")
CN = instrument("600519.SS")
TRADING_DAYS_2027: Final = 243


def official_holidays() -> frozenset[date]:
    days: set[date] = set()
    for start, end in OFFICIAL_RANGES:
        day = date.fromisoformat(start)
        while day <= date.fromisoformat(end):
            days.add(day)
            day += timedelta(days=1)
    return frozenset(days)


@pytest.mark.parametrize("offset", range(365))
def test_official_day_when_each_2027_date(offset: int) -> None:
    # Given: 2027 年每个日历日上午 10 点（北京时间）。
    day = date(2027, 1, 1) + timedelta(days=offset)
    local = datetime.combine(day, time(10), SHANGHAI)
    closed = day.weekday() >= 5 or day in official_holidays()
    # When / Then: 休市日不在连续交易时段；交易日 9:30 开盘、14:57 应用截止。
    assert is_open(CN, local) is (not closed)
    if not closed:
        opening, closing = window(CN, local)
        assert opening == datetime.combine(day, time(9, 30), SHANGHAI)
        assert closing == datetime.combine(day, time(14, 57), SHANGHAI)
        assert not is_open(CN, datetime.combine(day, time(12), SHANGHAI))


def test_session_count_and_makeup_weekends_when_2027() -> None:
    cal = calendar("CN")
    assert len(cal.sessions_in_range("2027-01-01", "2027-12-31")) == TRADING_DAYS_2027
    assert all(not cal.is_session(day) for day in MAKEUP_WEEKENDS)


def test_upstream_sessions_unchanged_when_before_2027() -> None:
    # Given: 上游 XSHG 原有覆盖范围。
    upstream = xcals.get_calendar("XSHG")
    ours = calendar("CN")
    # Then: 2026 及以前逐日完全一致，只在尾部追加 2027。
    first, last = upstream.first_session, upstream.last_session
    assert ours.sessions_in_range(first, last).equals(upstream.sessions_in_range(first, last))


def test_t1_unlock_crosses_year_end_and_spring_festival() -> None:
    # 2026-12-31 买入 → 2027-01-04 开盘可卖；春节前最后交易日 2027-02-04 买入 → 2027-02-15 开盘可卖。
    assert sellable_time(CN, datetime(2026, 12, 31, 10, tzinfo=SHANGHAI)) == datetime(
        2027, 1, 4, 9, 30, tzinfo=SHANGHAI
    )
    assert sellable_time(CN, datetime(2027, 2, 4, 10, tzinfo=SHANGHAI)) == datetime(2027, 2, 15, 9, 30, tzinfo=SHANGHAI)


def test_reject_when_beyond_2027_coverage() -> None:
    # 2027-12-31 盘中买入需要 2028 年下一交易日，仍按日历不足拒绝。
    with pytest.raises(DomainError) as error:
        _ = sellable_time(CN, datetime(2027, 12, 31, 10, tzinfo=SHANGHAI))
    assert error.value.code == "calendar_unavailable"


def covered_through_2027(_cls: type[XSHGExchangeCalendar]) -> pd.Timestamp:
    return pd.Timestamp("2027-12-31")


def test_supplement_disabled_when_upstream_covers_2027(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 上游假日表将来覆盖 2027。
    monkeypatch.setattr(XSHGExchangeCalendar, "bound_max", classmethod(covered_through_2027))
    calendar.cache_clear()
    try:
        # When: 经公共入口取得 A 股日历（模拟进程重载）。
        cal = calendar("CN")
        # Then: 直接使用上游日历，不再叠加本地补充。
        assert type(cal) is XSHGExchangeCalendar
        assert cal is xcals.get_calendar("XSHG")
    finally:
        calendar.cache_clear()
