"""按交易所日历处理假日、午休及美国夏令时。"""

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

import exchange_calendars as xcals

from app.models import DomainError, Instrument


@lru_cache(maxsize=3)
def calendar(market: str) -> xcals.ExchangeCalendar:
    """取得交易所假日与夏令时日历。"""
    return xcals.get_calendar({"CN": "XSHG", "HK": "XHKG", "US": "XNYS"}[market])


def window(item: Instrument, now: datetime) -> tuple[datetime, datetime]:
    """取得当前或下一支持交易日的生效和截止时间。"""
    cal = calendar(item.market)
    local_day = now.astimezone(cal.tz).date()
    try:
        session = cal.date_to_session(str(local_day), direction="next")
        adjustment = timedelta(minutes=3) if item.market == "CN" else timedelta(0)
        close = cal.session_close(session).to_pydatetime() - adjustment
        if now >= close:
            session = cal.next_session(session)
        return cal.session_open(session).to_pydatetime(), cal.session_close(session).to_pydatetime() - adjustment
    except (ValueError, IndexError) as error:
        raise DomainError("calendar_unavailable", "交易日历未覆盖此日期，暂停受理") from error


def is_open(item: Instrument, at: datetime) -> bool:
    """判断此时刻是否属于本应用支持的连续交易时段。"""
    cal = calendar(item.market)
    local = at.astimezone(cal.tz)
    if item.market == "CN" and (local.hour, local.minute) >= (14, 57):
        return False
    try:
        return bool(cal.is_open_on_minute(at.replace(second=0, microsecond=0), ignore_breaks=False))
    except (ValueError, IndexError):
        return False


def sellable_time(item: Instrument, event: datetime) -> datetime:
    """将交易日约束转换为实际可卖时刻。"""
    if item.settlement_days == 0:
        return event
    _, close = window(item, event)
    opening, _ = window(item, close + timedelta(seconds=1))
    return opening.astimezone(UTC)


def prior_session_day(item: Instrument, active_at: datetime) -> date:
    """日价格边界必须对应紧邻的前一交易日，不能沿用缺失多日的收盘价。"""
    cal = calendar(item.market)
    session = cal.date_to_session(str(active_at.astimezone(cal.tz).date()), direction="next")
    return cal.previous_session(session).to_pydatetime().date()
