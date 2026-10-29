"""委托解释仅在响应时投影，不改变账本或请求幂等记录。"""

from app.calendar import first_candidate_interval
from app.catalog import CATALOG, instrument
from app.models import CandidateInterval, DomainError, Instrument, Order, OrderView


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


def order_view(order: Order, catalog: tuple[Instrument, ...] = CATALOG) -> OrderView:
    """只扩展返回值，持久Order仍严格使用原字段。"""
    return OrderView.model_validate(
        {
            **order.model_dump(include=set(Order.model_fields)),
            "first_candidate_interval": candidate_interval(order, catalog),
        }
    )
