"""按交易所日历处理假日、午休及美国夏令时。"""

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Final, override

import exchange_calendars as xcals
import pandas as pd
from exchange_calendars.exchange_calendar_xshg import XSHGExchangeCalendar

from app.models import DomainError, Instrument

# 国务院办公厅国办发明电〔2026〕10号放假日中的工作日；调休上班的周末不开市。
# 截至 2026-11-28，锁定的 exchange-calendars 4.13.2 与最新 4.13.3 的 XSHG 均止于 2026-12-31；
# 上游覆盖 2027 后自动停用本补充。
CN_SUPPLEMENT_YEAR: Final = 2027
CN_2027_HOLIDAYS: tuple[str, ...] = (
    "2027-01-01",
    "2027-02-05",
    "2027-02-08",
    "2027-02-09",
    "2027-02-10",
    "2027-02-11",
    "2027-02-12",
    "2027-04-05",
    "2027-05-03",
    "2027-05-04",
    "2027-05-05",
    "2027-06-09",
    "2027-09-15",
    "2027-10-01",
    "2027-10-04",
    "2027-10-05",
    "2027-10-06",
    "2027-10-07",
)


class XSHG2027(XSHGExchangeCalendar):
    """在上游 XSHG 假日表后补 2027 年；交易时段、午休沿用上游。"""

    @classmethod
    @override
    def precomputed_holidays(cls) -> pd.DatetimeIndex:
        """上游假日表加 2027 年休市工作日。"""
        upstream = pd.DatetimeIndex(XSHGExchangeCalendar.precomputed_holidays())
        return upstream.append(pd.DatetimeIndex(CN_2027_HOLIDAYS)).sort_values()


def cn_calendar() -> xcals.ExchangeCalendar:
    """上游已覆盖 2027 时直接使用上游，否则使用本地补充。"""
    if XSHGExchangeCalendar.bound_max().year >= CN_SUPPLEMENT_YEAR:
        return xcals.get_calendar("XSHG")
    return XSHG2027(end=XSHG2027.bound_max())


@lru_cache(maxsize=3)
def calendar(market: str) -> xcals.ExchangeCalendar:
    """取得交易所假日与夏令时日历。"""
    if market == "CN":
        return cn_calendar()
    name = {"HK": "XHKG", "US": "XNYS"}[market]
    cal = xcals.get_calendar(name)
    if market in {"HK", "US"} and cal.default_end().to_pydatetime().date() < date(2028, 1, 3):
        cal = xcals.get_calendar(name, end="2028-01-03")
    return cal


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
    lunch_start = {"CN": time(11, 30), "HK": time(12)}.get(item.market)
    if lunch_start is not None and lunch_start <= local.time() < time(13):
        return False
    try:
        opening, closing = window(item, at)
        return opening <= at < closing and bool(
            cal.is_open_on_minute(at.replace(second=0, microsecond=0), ignore_breaks=False)
        )
    except (ValueError, IndexError, DomainError):
        return False


def segment_start(item: Instrument, at: datetime) -> datetime | None:
    """返回所在支持段起点；非支持时段与is_open一致地返回空。"""
    if not is_open(item, at):
        return None
    local = at.astimezone(calendar(item.market).tz)
    if item.market in {"CN", "HK"} and local.time() >= time(13):
        return local.replace(hour=13, minute=0, second=0, microsecond=0).astimezone(UTC)
    opening, _ = window(item, at)
    return opening


def previous_segment_end(item: Instrument, at: datetime) -> datetime | None:
    """返回当前支持段的上一段截止，沿用午休、假日和提前收盘日历。"""
    start = segment_start(item, at)
    if start is None:
        return None
    cal = calendar(item.market)
    local = start.astimezone(cal.tz)
    if item.market in {"CN", "HK"} and local.time() == time(13):
        lunch = time(11, 30) if item.market == "CN" else time(12)
        return local.replace(hour=lunch.hour, minute=lunch.minute).astimezone(UTC)
    adjustment = timedelta(minutes=3) if item.market == "CN" else timedelta(0)
    return prior_session_close(item, start) - adjustment


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


def last_candidate_end(item: Instrument, active_at: datetime, cutoff: datetime) -> datetime | None:
    """找出闭区间内最后一个完整五分钟连续交易区间的结束时刻。"""
    end = cutoff.replace(second=0, microsecond=0) - timedelta(minutes=cutoff.minute % 5)
    duration = timedelta(minutes=5)
    while end - duration >= active_at:
        if is_open(item, end - duration) and is_open(item, end - timedelta(microseconds=1)):
            return end
        end -= duration
    return None


def prior_session_close(item: Instrument, active_at: datetime) -> datetime:
    """参考日实际交易所收盘，不使用应用的提前截止。"""
    cal = calendar(item.market)
    session = cal.date_to_session(str(prior_session_day(item, active_at)), direction="previous")
    return cal.session_close(session).to_pydatetime()


def first_candidate_interval(
    item: Instrument, active_at: datetime, cutoff: datetime
) -> tuple[datetime, datetime] | None:
    """复用连续时段和闭区间规则，向上取整五分钟起点。"""
    cal = calendar(item.market)
    _ = cal.date_to_session(str(active_at.astimezone(cal.tz).date()), direction="next")
    _ = cal.date_to_session(str(cutoff.astimezone(cal.tz).date()), direction="previous")
    start = active_at.replace(second=0, microsecond=0) - timedelta(minutes=active_at.minute % 5)
    duration = timedelta(minutes=5)
    if start < active_at:
        start += duration
    while start + duration <= cutoff:
        end = start + duration
        if is_open(item, start) and is_open(item, end - timedelta(microseconds=1)):
            return start, end
        start = end
    return None
