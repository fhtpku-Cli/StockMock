"""委托解释仅在响应时投影，不改变账本或请求幂等记录。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.accounting import ACTIVE
from app.calendar import first_candidate_interval
from app.catalog import CATALOG, instrument
from app.models import CandidateInterval, DomainError, Instrument, Order, OrderView, WaitingView


@dataclass(frozen=True, slots=True)
class WaitingContext:
    """一次响应使用的暂停状态和已完成后台轮次的失败快照。"""

    now: datetime
    paused: frozenset[str]
    failures: Mapping[str, datetime]


def waiting_view(
    order: Order, context: WaitingContext, catalog: tuple[Instrument, ...] = CATALOG
) -> WaitingView | None:
    """按暂停、来源、截止和处理进度分类；终态与持久账本无关。"""
    if order.status not in ACTIVE:
        return None
    checked = order.last_sample_at
    if order.symbol in context.paused:
        return WaitingView(code="paused", message="已人工暂停，委托保留至截止或撤单确认", checked_through=checked)
    failed_at = context.failures.get(order.symbol)
    if failed_at is not None:
        since = failed_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        return WaitingView(
            code="source_failing",
            message=f"分钟线自北京时间 {since} 起获取失败，恢复后仍会先处理合格旧区间",
            checked_through=checked,
            since=failed_at,
        )
    cutoff = min(order.expires_at, order.cancel_at or order.expires_at)
    if context.now >= cutoff:
        return WaitingView(
            code="after_cutoff",
            message="已到截止，等待截止前区间确认，确认时间取决于行情到达情况",
            checked_through=checked,
        )
    if context.now < order.active_at:
        active = order.active_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        return WaitingView(code="not_active", message=f"尚未生效，将于北京时间 {active} 生效", checked_through=checked)
    first = candidate_interval(order, catalog)
    if checked is None or checked < order.active_at or first.end is None or checked < first.end:
        view = WaitingView(code="awaiting_first", message="已生效，等待首个候选完整区间处理", checked_through=checked)
    else:
        through = checked.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
        view = WaitingView(
            code="checked_not_met",
            message=f"本系统已处理至 {through}，尚未满足成交条件；不代表市场全部成交",
            checked_through=checked,
        )
    return view


def candidate_interval(order: Order, catalog: tuple[Instrument, ...] = CATALOG) -> CandidateInterval:
    """日历或旧证券不可解析时诚实返回未知，已有委托仍可读取。"""
    try:
        interval = first_candidate_interval(
            instrument(order.symbol, catalog),
            order.active_at,
            min(order.expires_at, order.cancel_at or order.expires_at),
        )
    except (DomainError, ValueError, IndexError, KeyError):
        return CandidateInterval(status="unknown", reason="交易日历或证券规则不可用，首个候选区间未知")
    if interval is None:
        return CandidateInterval(status="none", reason="生效至截止没有完整候选区间，不会新增成交机会，等待后台确认")
    return CandidateInterval(
        status="available",
        start=interval[0],
        end=interval[1],
        reason="首个候选完整区间；有效行情到达并满足价格条件才可能成交，不是预计成交时间",
    )


def order_view(
    order: Order, catalog: tuple[Instrument, ...] = CATALOG, *, waiting: WaitingContext | None = None
) -> OrderView:
    """只扩展返回值，持久Order仍严格使用原字段。"""
    return OrderView.model_validate(
        {
            **order.model_dump(include=set(Order.model_fields)),
            "first_candidate_interval": candidate_interval(order, catalog),
            "waiting": waiting_view(order, waiting, catalog) if waiting is not None else None,
        }
    )
