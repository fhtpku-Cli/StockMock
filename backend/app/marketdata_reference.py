"""仅供展示的报价昨收与图表日线后置校验；不进入成交依据，也不改变原有解析。"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from app.marketdata_models import Bar, MarketDataError, Quote


@dataclass(frozen=True, slots=True)
class QuoteReference:
    """同一次报价响应中的现价快照与供应商昨收。"""

    quote: Quote
    previous_close: Decimal | None


def parse_previous_close(raw: bytes, code: str) -> Decimal | None:
    """读取腾讯报价字段 4；无法得到有限正数时返回空，不影响现价解析。"""
    try:
        text = raw.decode("gb18030").strip()
        prefix = f'v_{code}="'
        if not text.startswith(prefix) or not text.endswith('";'):
            return None
        value = Decimal(text[len(prefix) : -2].split("~")[4])
    except (UnicodeError, IndexError, InvalidOperation):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value


def checked_chart_bars(bars: tuple[Bar, ...]) -> tuple[Bar, ...]:
    """图表专用：日期必须严格升序且不重复，否则整组不展示。"""
    if any(later.day <= earlier.day for earlier, later in pairwise(bars)):
        raise MarketDataError("invalid_chart_history", bars[0].symbol)
    return bars
