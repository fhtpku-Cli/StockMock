"""提交价步长与数量规则，撮合价保留来源实际成交精度。"""

from decimal import Decimal
from typing import assert_never

from app.market_ticks import hk_tick_size
from app.models import DomainError, Instrument, OrderInput, State


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
    """买入遵循最小量与步长；港股不借用整手报价处理零股。"""
    step = item.quantity_step or item.lot_size
    minimum = item.min_buy_quantity or item.lot_size
    total = sum(lot.quantity for lot in state.lots if lot.symbol == item.symbol)
    if request.side == "sell" and request.quantity == total and item.market != "HK":
        return
    if request.quantity % step or (request.side == "buy" and request.quantity < minimum):
        restriction = "港股暂不支持零股" if item.market == "HK" else "零股仅可一次卖清"
        raise DomainError("invalid_lot", f"买入最少 {minimum} 股，数量按 {step} 股递增；{restriction}", 422)
