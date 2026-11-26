"""最近 3 个北京自然日内结束的委托摘要，只读当前轮次，不改账户。"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Final, Literal

from app.cash_queries import BEIJING
from app.models import Currency, Instrument, Model, Order, State

EndedStatus = Literal["filled", "cancelled", "expired", "terminated"]
LIMIT: Final = 10
WINDOW_DAYS: Final = 3
CURRENCIES: Final[tuple[Currency, ...]] = ("CNY", "USD", "HKD")


class CashChange(Model):
    """同一委托在一个币种上的流水净额，含成交、费用与自动换汇。"""

    currency: Currency
    amount: Decimal


class RecentEnding(Model):
    """一笔已结束委托的结果与对应资金或持仓变化。"""

    order_id: str
    symbol: str
    name: str | None
    side: Literal["buy", "sell"]
    quantity: int
    status: EndedStatus
    status_reason: str
    currency: Currency
    fill_price: Decimal | None
    filled_at: datetime | None
    ended_at: datetime
    cash_changes: list[CashChange]
    released_cny: Decimal | None
    released_foreign: Decimal | None
    released_shares: int | None


class RecentEndings(Model):
    """窗口为北京时间今天往前 2 天的 00:00 至当前时刻，含两端。"""

    window_start: datetime
    window_end: datetime
    items: list[RecentEnding]
    more: bool


def ended_status(order: Order) -> EndedStatus | None:
    """撤单待确认与已受理仍属进行中。"""
    match order.status:
        case "filled" | "cancelled" | "expired" | "terminated":
            return order.status
        case "accepted" | "cancel_pending":
            return None


def ended_at(order: Order, status: EndedStatus) -> datetime | None:
    """优先确认时间；生效前撤销只写撤单时间；都缺失时不推算。"""
    if order.confirmed_at is not None:
        return order.confirmed_at
    if status == "cancelled" and order.cancel_at is not None:
        return order.cancel_at
    return None


def cash_changes(state: State, order_id: str) -> list[CashChange]:
    """按币种用十进制原值求和，净额为 0 的币种省略。"""
    totals: defaultdict[Currency, Decimal] = defaultdict(Decimal)
    for entry in state.ledger:
        if entry.order_id == order_id:
            totals[entry.currency] += entry.amount
    return [
        CashChange(currency=currency, amount=totals[currency])
        for currency in CURRENCIES
        if totals.get(currency, Decimal(0)) != 0
    ]


def ending(state: State, order: Order, status: EndedStatus, end: datetime, name: str | None) -> RecentEnding:
    """未成交结束时，买单展示原预留资金，卖单展示释放的持仓数量。"""
    unfilled = status != "filled"
    buy = order.side == "buy"
    return RecentEnding(
        order_id=order.id,
        symbol=order.symbol,
        name=name,
        side=order.side,
        quantity=order.quantity,
        status=status,
        status_reason=order.status_reason,
        currency=order.currency,
        fill_price=order.fill_price,
        filled_at=order.filled_at,
        ended_at=end,
        cash_changes=cash_changes(state, order.id),
        released_cny=order.reserved_cny if unfilled and buy else None,
        released_foreign=order.reserved_foreign if unfilled and buy else None,
        released_shares=order.quantity if unfilled and not buy else None,
    )


def recent_endings(state: State, catalog: tuple[Instrument, ...], now: datetime) -> RecentEndings:
    """按结束时间倒序、同刻按委托编号升序，最多 10 条。"""
    start_day = now.astimezone(BEIJING).date() - timedelta(days=WINDOW_DAYS - 1)
    start = datetime.combine(start_day, time(0), tzinfo=BEIJING)
    candidates: list[tuple[datetime, EndedStatus, Order]] = []
    for order in state.orders:
        status = ended_status(order)
        end = ended_at(order, status) if status is not None else None
        if status is not None and end is not None and start <= end <= now:
            candidates.append((end, status, order))
    candidates.sort(key=lambda candidate: candidate[2].id)
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    names = {item.symbol: item.name for item in catalog}
    return RecentEndings(
        window_start=start,
        window_end=now,
        items=[ending(state, order, status, end, names.get(order.symbol)) for end, status, order in candidates[:LIMIT]],
        more=len(candidates) > LIMIT,
    )
