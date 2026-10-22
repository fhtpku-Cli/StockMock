"""港交所 2026-08-03 起股票价位表与独立 ETF 价位表。"""

from decimal import Decimal
from typing import Final, Literal, assert_never

from app.marketdata_models import MarketDataError

_STOCK: Final = (
    (".25", ".001"),
    ("10", ".005"),
    ("20", ".01"),
    ("50", ".02"),
    ("100", ".05"),
    ("200", ".1"),
    ("500", ".2"),
    ("1000", ".5"),
    ("2000", "1"),
    ("5000", "2"),
    ("9995", "5"),
)
_ETF: Final = (
    ("1", ".001"),
    ("5", ".002"),
    ("10", ".005"),
    ("20", ".01"),
    ("100", ".02"),
    ("200", ".05"),
    ("500", ".1"),
    ("1000", ".2"),
    ("2000", ".5"),
    ("9999", "1"),
)


def hk_tick_size(price: Decimal, kind: Literal["stock", "ETF"] = "stock") -> Decimal:
    """边界价格属于前一档；超出交易所支持价格范围时明确拒绝。"""
    if not price.is_finite() or price < Decimal(".01"):
        raise MarketDataError("unsupported_price", "HK")
    match kind:
        case "stock":
            table = _STOCK
        case "ETF":
            table = _ETF
        case _:
            assert_never(kind)
    for upper, tick in table:
        if price <= Decimal(upper):
            return Decimal(tick)
    raise MarketDataError("unsupported_price", "HK")
