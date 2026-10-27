"""金额、预留和成交记账，所有更新由外层事务保护。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Final, assert_never
from uuid import uuid4

from app.calendar import calendar, sellable_time
from app.marketdata import Quote
from app.models import Currency, DomainError, Entry, Instrument, Lot, Order, State

CENT: Final = Decimal(".01")
FEE: Final = Decimal(".0005")
ACTIVE: Final = frozenset({"accepted", "cancel_pending"})


def money(value: Decimal, *, reserve: bool = False) -> Decimal:
    """按最小货币单位舍入，预留时向上取整。"""
    return value.quantize(CENT, rounding=ROUND_CEILING if reserve else ROUND_HALF_UP)


def execution_amount(price: Decimal, quantity: int) -> tuple[Decimal, Decimal]:
    """唯一成交金额算法：成交额和费用分别四舍五入到分。"""
    gross = money(price * quantity)
    return gross, money(gross * FEE)


def reserved(state: State, currency: Currency) -> Decimal:
    """汇总活动委托对指定币种的占用。"""
    return sum(
        (
            o.reserved_cny if currency == "CNY" else o.reserved_foreign
            for o in state.orders
            if o.status in ACTIVE and currency in ("CNY", o.currency)
        ),
        Decimal(0),
    )


def available(state: State, currency: Currency) -> Decimal:
    """返回扣除全部活动委托占用后的现金。"""
    return state.cash[currency] - reserved(state, currency)


def sellable(state: State, symbol: str, now: datetime) -> int:
    """扣除未解锁批次与卖单占用后的可卖数量。"""
    total = sum(lot.quantity for lot in state.lots if lot.symbol == symbol and lot.sellable_at <= now)
    held = sum(o.quantity for o in state.orders if o.symbol == symbol and o.side == "sell" and o.status in ACTIVE)
    return total - held


@dataclass(frozen=True, slots=True)
class EntryContext:
    """将流水的发生时刻、关联委托和说明一起保存。"""

    now: datetime
    order_id: str | None
    note: str


def entry(
    state: State,
    kind: str,
    currency: Currency,
    amount: Decimal,
    context: EntryContext,
) -> None:
    """在当前事务聚合追加一条现金流水。"""
    state.ledger.append(
        Entry(
            id=str(uuid4()),
            kind=kind,
            currency=currency,
            amount=amount,
            created_at=context.now,
            order_id=context.order_id,
            note=context.note,
        )
    )


def fill(
    state: State,
    order: Order,
    item: Instrument,
    sample: Quote,
    now: datetime,
) -> Order:
    """按有效样本同步更新现金、成本、份额和费用。"""
    price, event = sample.price, sample.market_time
    gross, fee = execution_amount(price, order.quantity)
    reason = "按有效市场样本整笔成交；不模拟盘口排队"
    if order.cancel_at is not None:
        reason += "；撤单截止前已有满足条件的完整区间，撤单未生效"
    if now - event > timedelta(minutes=20):
        market_time = event.astimezone(calendar(item.market).tz).strftime("%H:%M")
        reason += f"；后台延迟处理，按市场时间 {market_time} 的完整区间成交"
    cost_basis = realized_pnl = None
    match order.side:
        case "buy":
            cost = gross + fee
            foreign = min(cost, order.reserved_foreign)
            cny = cost if order.currency == "CNY" else money((cost - foreign) * order.fx_rate)
            if cny > order.reserved_cny or state.cash["CNY"] < cny or state.cash[order.currency] < foreign:
                raise DomainError("reservation_insufficient", "预留资金不足，无法成交")
            unlock = sellable_time(item, event)
            if order.currency != "CNY":
                converted = cost - foreign
                state.cash["CNY"] -= cny
                state.cash[order.currency] += converted
                if converted:
                    entry(
                        state,
                        "fx_out",
                        "CNY",
                        -cny,
                        EntryContext(now, order.id, f"按委托参考汇率 {order.fx_rate} 换汇"),
                    )
                    entry(state, "fx_in", order.currency, converted, EntryContext(now, order.id, order.fx_source))
            state.cash[order.currency] -= cost
            entry(state, "buy", order.currency, -gross, EntryContext(now, order.id, order.symbol))
            entry(state, "fee", order.currency, -fee, EntryContext(now, order.id, "模拟费率 0.05%"))
            state.lots.append(
                Lot(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    cost=cost,
                    sellable_at=unlock,
                )
            )
        case "sell":
            cost_basis = reduce_lots(state, order, event)
            realized_pnl = gross - fee - cost_basis
            state.cash[order.currency] += gross - fee
            entry(state, "sell", order.currency, gross, EntryContext(now, order.id, order.symbol))
            entry(state, "fee", order.currency, -fee, EntryContext(now, order.id, "模拟费率 0.05%"))
        case _:
            assert_never(order.side)
    return order.model_copy(
        update={
            "status": "filled",
            "status_reason": reason,
            "filled_at": event,
            "confirmed_at": now,
            "fill_price": price,
            "fee": fee,
            "cost_basis": cost_basis,
            "realized_pnl": realized_pnl,
        }
    )


def reduce_lots(state: State, order: Order, event: datetime) -> Decimal:
    """按移动加权平均成本结转售出份额，保留剩余批次解锁日期。"""
    remaining = order.quantity
    lots: list[Lot] = []
    # 每个原始买入批次均按自身平均成本结转，整体平均成本不变。
    eligible = [lot for lot in state.lots if lot.symbol == order.symbol and lot.sellable_at <= event]
    eligible_quantity = sum(lot.quantity for lot in eligible)
    if eligible_quantity < remaining:
        raise DomainError("shares_unavailable", "可卖数量不足")
    total_cost = sum(
        (lot.cost for lot in state.lots if lot.symbol == order.symbol),
        Decimal(0),
    )
    total_quantity = sum(lot.quantity for lot in state.lots if lot.symbol == order.symbol)
    removed_cost = money(total_cost * order.quantity / total_quantity)
    for lot in state.lots:
        take = min(remaining, lot.quantity) if lot in eligible else 0
        remaining -= take
        if lot.quantity > take:
            lots.append(lot.model_copy(update={"quantity": lot.quantity - take}))
    residual = [lot for lot in lots if lot.symbol == order.symbol]
    residual_qty = sum(lot.quantity for lot in residual)
    allocation = total_cost - removed_cost
    normalized: list[Lot] = []
    for lot in lots:
        if lot.symbol == order.symbol:
            cost = money((total_cost - removed_cost) * lot.quantity / residual_qty)
            if lot is residual[-1]:
                cost = allocation
            allocation -= cost
            updated_lot = lot.model_copy(update={"cost": cost})
        else:
            updated_lot = lot
        normalized.append(updated_lot)
    state.lots[:] = normalized
    return removed_cost
