"""单个市场样本的时间、价格与成交判断。"""

from datetime import datetime, timedelta

from app.accounting import fill
from app.calendar import is_open, last_candidate_end
from app.marketdata import Quote
from app.models import Instrument, Order, State


def eligible_time(order: Order, item: Instrument, sample: Quote) -> bool:
    """要求整个分钟区间处于委托生效后且为可支持时段。"""
    event = sample.market_time
    start = sample.interval_start
    return (
        start is not None
        and sample.volume is not None
        and sample.volume > 0
        and start < event
        and event - start <= timedelta(minutes=5)
        and event > order.created_at
        and event > order.active_at
        and sample.price > 0
        and start >= order.active_at
        and is_open(item, event - timedelta(microseconds=1))
        and is_open(item, start)
    )


def daily_blocked(order: Order, sample: Quote) -> bool:
    """无盘口时不在买入涨停或卖出跌停边界假定可成交。"""
    return (
        order.daily_lower is not None
        and order.daily_upper is not None
        and (
            sample.price < order.daily_lower
            or sample.price > order.daily_upper
            or (order.side == "buy" and sample.price == order.daily_upper)
            or (order.side == "sell" and sample.price == order.daily_lower)
        )
    )


def advance(state: State, order: Order, item: Instrument, sample: Quote, now: datetime) -> Order:
    """只消费同源有效新样本，结束状态由调用者停止继续处理。"""
    event = sample.market_time
    if (
        sample.symbol != order.symbol
        or sample.source != order.source
        or sample.currency != item.currency
        or event > now
        or not sample.tradable
        or (order.last_sample_at is not None and event <= order.last_sample_at)
    ):
        return order
    cutoff = min(order.expires_at, order.cancel_at or order.expires_at)
    if event > cutoff:
        return order.model_copy(
            update={
                "status": "cancelled" if order.cancel_at and order.cancel_at <= order.expires_at else "expired",
                "status_reason": "已采集数据推进至委托截止，释放占用",
                "confirmed_at": now,
            }
        )
    updated = order.model_copy(update={"last_sample_at": event})
    if not eligible_time(updated, item, sample):
        return updated
    allowed = sample.price <= order.price_bound if order.side == "buy" else sample.price >= order.price_bound
    blocked = daily_blocked(order, sample)
    if allowed and not blocked:
        return fill(state, updated, item, sample, now)
    if order.order_type == "market":
        return updated.model_copy(
            update={
                "status": "terminated",
                "confirmed_at": now,
                "status_reason": "样本处于不支持的日价格边界，无盘口不能确认成交"
                if blocked
                else "首个有效样本超出 2% 保护范围",
            }
        )
    return updated


def confirm_cutoff(order: Order, item: Instrument, now: datetime, *, allow_timeout: bool) -> Order:
    """在截止后按最后候选区间水位确认结果，故障保护只控制数据超时。"""
    cutoff = min(order.expires_at, order.cancel_at or order.expires_at)
    if now < cutoff:
        return order
    last = last_candidate_end(item, order.active_at, cutoff)
    reason = None
    if last is None:
        reason = "截止前没有可成交的完整区间"
    elif order.last_sample_at is not None and order.last_sample_at >= last:
        reason = "截止前全部可成交区间已处理，未满足条件"
    if reason is not None:
        return order.model_copy(
            update={
                "status": "cancelled" if order.cancel_at and order.cancel_at <= order.expires_at else "expired",
                "status_reason": reason,
                "confirmed_at": now,
            }
        )
    if last is not None and allow_timeout and now > last + timedelta(minutes=20):
        return order.model_copy(
            update={
                "status": "terminated",
                "status_reason": "数据不足，未确认成交；释放占用，不回填迟到数据",
                "confirmed_at": now,
            }
        )
    return order
