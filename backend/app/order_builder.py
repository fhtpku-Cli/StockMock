"""委托价格与资源预留计算。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import assert_never
from uuid import uuid4

from app.accounting import FEE, available, money, sellable
from app.calendar import is_open, window
from app.marketdata import FxRate, Quote
from app.models import DomainError, Instrument, Order, OrderInput, State
from app.order_rules import tick_size, validate_quantity


@dataclass(frozen=True, slots=True)
class OrderContext:
    """事务外取得的行情、汇率、时间和昨收参考。"""

    quote: Quote
    rate: FxRate | None
    now: datetime
    reference: Decimal | None


def price_bound(item: Instrument, request: OrderInput, quote: Quote, now: datetime) -> Decimal:
    """确定限价或按报价步长保守舍入的市价保护边界。"""
    match request.order_type:
        case "limit":
            if request.limit_price is None:
                raise DomainError("invalid_price", "请输入限价", 422)
            step = tick_size(item, request.limit_price)
            if request.limit_price % step:
                raise DomainError("invalid_price", f"请输入符合 {step} 步长的限价", 422)
            return request.limit_price
        case "market":
            if not is_open(item, now):
                raise DomainError("market_closed", "市价模拟只在常规连续交易时段受理")
            factor = Decimal("1.02") if request.side == "buy" else Decimal(".98")
            rounding = ROUND_FLOOR if request.side == "buy" else ROUND_CEILING
            raw = quote.price * factor
            step = tick_size(item, raw)
            bound = (raw / step).to_integral_value(rounding=rounding) * step
            actual_step = tick_size(item, bound)
            return (bound / actual_step).to_integral_value(rounding=rounding) * actual_step
        case _:
            assert_never(request.order_type)


def build_order(item: Instrument, state: State, request: OrderInput, context: OrderContext) -> Order:
    """校验价格与数量，计算订单参考汇率及资源预留。"""
    quote, rate, now, reference = context.quote, context.rate, context.now, context.reference
    validate_quantity(item, state, request)
    opening, closing = window(item, now)
    bound = price_bound(item, request, quote, now)
    lower = upper = None
    if reference is not None:
        fraction = item.price_limit_ratio
        if fraction is None:
            raise DomainError("unsupported_price_limit", "缺少已核验日价格范围，不受理该证券")
        lower = ((reference * (1 - fraction)) / item.tick_size).quantize(
            Decimal(1), rounding="ROUND_HALF_UP"
        ) * item.tick_size
        upper = ((reference * (1 + fraction)) / item.tick_size).quantize(
            Decimal(1), rounding="ROUND_HALF_UP"
        ) * item.tick_size
        if request.order_type == "limit" and not lower <= bound <= upper:
            raise DomainError("daily_price_limit", "限价超过本标的已支持的日价格范围")
    fx = rate.cny_per_unit if rate is not None else Decimal(1)
    cny = foreign = Decimal(0)
    match request.side:
        case "buy":
            total = money(bound * request.quantity * (1 + FEE), reserve=True)
            foreign = min(total, available(state, item.currency)) if item.currency != "CNY" else Decimal(0)
            if item.currency != "CNY" and total > foreign and rate is None:
                raise DomainError("fx_unavailable", "需自动换汇，但参考汇率不可用或过期")
            cny = money((total - foreign) * fx, reserve=True)
            if available(state, "CNY") < cny:
                raise DomainError(
                    "insufficient_funds",
                    "可用资金不足，其他委托占用的资金不能重复使用",
                )
        case "sell":
            if sellable(state, item.symbol, now) < request.quantity:
                raise DomainError(
                    "insufficient_shares",
                    "可卖持仓不足，可能尚未到可卖交易日或被其他委托占用",
                )
        case _:
            assert_never(request.side)
    return Order(
        request_id=request.request_id,
        symbol=request.symbol,
        side=request.side,
        order_type=request.order_type,
        quantity=request.quantity,
        limit_price=request.limit_price,
        reason=request.reason,
        target_price=request.target_price,
        stop_price=request.stop_price,
        id=str(uuid4()),
        price_bound=bound,
        created_at=now,
        active_at=max(now, opening),
        expires_at=closing,
        currency=item.currency,
        reserved_cny=cny,
        reserved_foreign=foreign,
        fx_rate=fx,
        fx_source=rate.source if rate else "",
        fx_time=rate.market_time if rate else None,
        source=quote.source,
        daily_lower=lower,
        daily_upper=upper,
    )
