"""提交价步长与数量规则，撮合价保留来源实际成交精度。"""

from decimal import Decimal
from typing import Final, Literal, assert_never

from app.market_ticks import hk_tick_size
from app.models import DomainError, Instrument, OrderInput, State

_ORDER_MAXIMUM: Final = 10_000_000
_CN_MAXIMUM: Final = 1_000_000
_CHINEXT_MAXIMUM: Final = {"limit": 300_000, "market": 150_000}
_STAR_MAXIMUM: Final = {"limit": 100_000, "market": 50_000}


def maximum_quantity(item: Instrument, order_type: Literal["limit", "market"]) -> int:
    """已支持A股及股票ETF共用交易所单笔上限，美港股保留输入上限。"""
    if item.market != "CN":
        return _ORDER_MAXIMUM
    if item.kind == "stock" and item.symbol.startswith("688"):
        return _STAR_MAXIMUM[order_type]
    if item.kind == "stock" and item.symbol.startswith(("300", "301")):
        return _CHINEXT_MAXIMUM[order_type]
    return _CN_MAXIMUM


def tick_size(item: Instrument, price: Decimal) -> Decimal:
    """港股依价格档，美股一美元以下允许万分位提交价。"""
    match item.tick_rule:
        case "fixed":
            return item.tick_size
        case "HK":
            return hk_tick_size(price, item.kind)
        case "US":
            return Decimal(".0001") if price < 1 else Decimal(".01")
        case _:
            assert_never(item.tick_rule)


def validate_quantity(item: Instrument, state: State, request: OrderInput) -> None:
    """买卖遵循最小量与步长，非港股不足最小量持仓允许一次卖清。"""
    maximum = maximum_quantity(item, request.order_type)
    if request.quantity > maximum:
        raise DomainError("quantity_limit", f"该委托类型每笔最多 {maximum} 股，请分笔操作", 422)
    step = item.quantity_step or item.lot_size
    minimum = item.min_buy_quantity or item.lot_size
    total = sum(lot.quantity for lot in state.lots if lot.symbol == item.symbol)
    if request.side == "sell" and request.quantity == total and item.market != "HK":
        return
    if request.quantity % step or request.quantity < minimum:
        restriction = "港股暂不支持零股" if item.market == "HK" else "零股仅可一次卖清"
        raise DomainError("invalid_lot", f"每笔最少 {minimum} 股，数量按 {step} 股递增；{restriction}", 422)
