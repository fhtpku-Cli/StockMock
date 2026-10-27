"""行情边界共享的不可变数据类型与明确错误。"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal

Currency = Literal["CNY", "HKD", "USD"]
SETTLE: Final = timedelta(seconds=90)
US_SETTLE: Final = timedelta(seconds=1020)


def interval_is_settled(end: datetime, retrieved_at: datetime, settle: timedelta) -> bool:
    """统一检验来源的候选定稿余量；后继出现不能替代等待。"""
    return end <= retrieved_at and retrieved_at >= end + settle


class MarketDataError(Exception):
    """稳定错误码供 API 展示，不自动替换数据源。"""

    def __init__(self, code: str, detail: str) -> None:
        """保留机器可读错误码和可显示的简短原因。"""
        self.code: str = code
        self.detail: str = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class Quote:
    """具有来源时间的估值快照或分钟区间候选。"""

    symbol: str
    price: Decimal
    currency: Currency
    market_time: datetime
    retrieved_at: datetime
    source: str
    tradable: bool = False
    interval_start: datetime | None = None
    volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Bar:
    """仅用于图表的未复权日线，成交量保留上游单位。"""

    symbol: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    currency: Currency
    market_time: datetime
    retrieved_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class FxRate:
    """每单位指定币种对应的人民币日频参考值。"""

    currency: Currency
    cny_per_unit: Decimal
    market_time: datetime
    retrieved_at: datetime
    source: str
