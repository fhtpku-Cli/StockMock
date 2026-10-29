"""按既有交易所日历投影支持时段；不读取行情或账户。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from exchange_calendars import ExchangeCalendar, Timestamp

from app.calendar import calendar
from app.market_session_models import TIMEZONES, Market, MarketSessionView, Phase, SupportPeriod

logger: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionPeriods:
    """单个交易日已校验的支持段与实际收盘。"""

    periods: tuple[SupportPeriod, ...]
    close: datetime


def _periods(cal: ExchangeCalendar, session: Timestamp, market: Market) -> SessionPeriods:
    opening = cal.session_open(session).to_pydatetime()
    closing = cal.session_close(session).to_pydatetime()
    end = closing - (timedelta(minutes=3) if market == "CN" else timedelta())
    first = cal.session_break_start(session).to_pydatetime()
    last = cal.session_break_end(session).to_pydatetime()
    # 上游 NaT 的 ISO 表示固定为 NaT；两个缺失才表示无午休。
    missing_first, missing_last = first.isoformat() == "NaT", last.isoformat() == "NaT"
    if missing_first and missing_last:
        return SessionPeriods((SupportPeriod(start=opening, end=end),), closing)
    if missing_first or missing_last or not opening < first < last < end:
        raise ValueError("invalid_calendar_break")
    return SessionPeriods((SupportPeriod(start=opening, end=first), SupportPeriod(start=last, end=end)), closing)


def _next(cal: ExchangeCalendar, session: Timestamp, market: Market, now: datetime) -> SupportPeriod | None:
    """未来日历失败只影响下一段，不抹去当前已知事实。"""
    try:
        period = _periods(cal, cal.next_session(session), market).periods[0]
        if period.start <= now:
            logger.warning("market_session future_unavailable market=%s category=invalid_future_session", market)
            return None
    except (ValueError, IndexError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError) as error:
        logger.warning("market_session future_unavailable market=%s category=%s", market, type(error).__name__)
        return None
    return period


def project(market: Market, now: datetime) -> MarketSessionView:
    """单次服务端时刻生成投影，当前和未来日历分别降级。"""
    zone = TIMEZONES[market]
    day = now.astimezone(ZoneInfo(zone)).date()
    current: SupportPeriod | None = None
    following: SupportPeriod | None = None
    phase: Phase = "unknown"
    try:
        cal = calendar(market)
        session = cal.date_to_session(str(day), direction="next")
        session_day = session.to_pydatetime().date()
        if session_day > day:
            phase = "non_session_day"
            # 查询日可定位，未来段结构坏时仍保留非交易日事实。
            try:
                following = _periods(cal, session, market).periods[0]
            except (ValueError, IndexError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError) as error:
                logger.warning("market_session future_unavailable market=%s category=%s", market, type(error).__name__)
        else:
            data = _periods(cal, session, market)
            current = next((p for p in data.periods if p.start <= now < p.end), None)
            following = next((p for p in data.periods if p.start > now), None)
            if current is not None:
                phase = "supported"
            elif now < data.periods[0].start:
                phase = "before_open"
            elif now < data.periods[-1].end:
                phase = "break"
            elif now < data.close:
                phase = "unsupported_closing"
            else:
                phase = "after_hours"
            if following is None:
                following = _next(cal, session, market, now)
    except (ValueError, IndexError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError) as error:
        logger.warning("market_session current_unavailable market=%s category=%s", market, type(error).__name__)
        phase, current, following = "unknown", None, None
    return MarketSessionView(
        market=market,
        as_of=now.astimezone(UTC),
        market_timezone=zone,
        market_date=day,
        phase=phase,
        current_period=current,
        next_period=following,
        reason="calendar_unavailable" if phase == "unknown" else None,
    )
