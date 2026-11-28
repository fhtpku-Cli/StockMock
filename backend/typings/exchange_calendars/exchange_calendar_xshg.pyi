# 仅声明 A 股日历补充所需的上游 XSHG 接口；运行仍调用实际依赖。
from datetime import tzinfo

from exchange_calendars import ExchangeCalendar
from pandas import DatetimeIndex, Timestamp

class XSHGExchangeCalendar(ExchangeCalendar):
    tz: tzinfo
    first_session: Timestamp
    last_session: Timestamp
    def __init__(self, *, end: Timestamp | str | None = None) -> None: ...
    @classmethod
    def precomputed_holidays(cls) -> DatetimeIndex: ...
    @classmethod
    def bound_max(cls) -> Timestamp: ...
