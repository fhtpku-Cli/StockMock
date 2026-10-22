"""按真实预留算法计算数量快捷键，不在浏览器用浮点金额估算。"""

from datetime import datetime
from typing import Final

from app.accounting import sellable
from app.catalog import instrument
from app.engine import Engine
from app.models import DomainError, OrderInput
from app.order_builder import OrderContext, build_order
from app.order_rules import maximum_quantity
from app.personal_models import Sizing

FULL_POSITION: Final = 100


def size_order(engine: Engine, request: OrderInput, now: datetime, percent: int) -> Sizing:
    """二分查找满足手续费、汇率及既有占用的最大数量；不预留资金。"""
    quote, rate, reference = engine.prepare(request, now)
    item = instrument(request.symbol, engine.catalog)
    state = engine.store.read()
    step = item.quantity_step or item.lot_size
    minimum = item.min_buy_quantity or item.lot_size
    limit = maximum_quantity(item, request.order_type)
    total = sum(lot.quantity for lot in state.lots if lot.symbol == item.symbol)
    maximum = 0
    if request.side == "sell":
        maximum = min(sellable(state, item.symbol, now), limit)
        if item.market == "HK" or maximum != total:
            maximum = maximum // step * step
        if maximum < minimum and (item.market == "HK" or maximum != total):
            maximum = 0
    else:
        low, high = 0, limit // step
        while low < high:
            middle = (low + high + 1) // 2
            quantity = middle * step
            if quantity < minimum:
                low = middle
                continue
            try:
                _ = build_order(
                    item,
                    state,
                    request.model_copy(update={"quantity": quantity}),
                    OrderContext(quote, rate, now, reference),
                )
                low = middle
            except DomainError as error:
                if error.code not in {"insufficient_funds", "fx_unavailable"}:
                    raise
                high = middle - 1
        maximum = low * step if low * step >= minimum else 0
    suggested = maximum if percent == FULL_POSITION else (maximum * percent // FULL_POSITION // step) * step
    if suggested < minimum and not (request.side == "sell" and item.market != "HK" and suggested == total):
        suggested = 0
    return Sizing(max_quantity=maximum, suggested_quantity=suggested, quantity_step=step, minimum_quantity=minimum)
