"""只组合已有事实；不补造计划历史或终态时间。"""

from datetime import UTC, datetime
from typing import assert_never

from app.models import Order
from app.order_views import order_view
from app.review_conclusions import detail
from app.review_core_models import SubjectType
from app.review_models import Fact, ReviewDetail
from app.review_repository import ReviewContext


def detail_facts(context: ReviewContext, loaded: ReviewDetail) -> list[Fact]:
    """系统确认时间排序；缺少确认时间的成交仍保留在末尾。"""
    result: list[Fact] = []
    plan = loaded.review.plan
    if plan:
        result.append(
            Fact(
                id=f"plan_created:{plan.id}",
                type="plan_created",
                record_id=plan.id,
                occurred_at=plan.created_at,
            )
        )
        if plan.updated_at != plan.created_at:
            result.append(
                Fact(
                    id=f"plan_updated:{plan.id}",
                    type="plan_updated",
                    record_id=plan.id,
                    occurred_at=plan.updated_at,
                    plan=plan,
                )
            )
    result.extend(fact for order in loaded.orders for fact in order_facts(order))
    result.extend(
        Fact(
            id=f"alert:{a.id}",
            type="alert",
            record_id=a.id,
            occurred_at=a.created_at,
            market_time=a.market_time,
            alert=a,
        )
        for a in context.alerts
        if plan and a.plan_id == plan.id
    )
    result.extend(
        Fact(id=f"note:{n.id}", type="note", record_id=n.id, occurred_at=n.created_at, note=n)
        for n in loaded.review.notes
    )
    result.extend(
        Fact(id=f"conclusion:{c.id}", type="conclusion", record_id=c.id, occurred_at=c.created_at, conclusion=c)
        for c in loaded.conclusions
    )
    return sorted(
        result,
        key=lambda f: (f.occurred_at is None, f.occurred_at or datetime.max.replace(tzinfo=UTC), f.type, f.record_id),
    )


def facts(context: ReviewContext, subject: tuple[SubjectType, str], now: datetime) -> list[Fact]:
    """与详情共享同一上下文，事件完整性不依赖旧详情上限。"""
    return detail_facts(context, detail(context, subject, now))


def order_facts(order: Order) -> list[Fact]:
    """委托时间来自已有字段，缺失确认时间保留未知。"""
    without_observation = order.model_copy(update={"fill_observation": None})
    result: list[Fact] = []
    result.append(
        Fact(
            id=f"order_submitted:{order.id}",
            type="order_submitted",
            record_id=order.id,
            occurred_at=order.created_at,
            order=order_view(without_observation),
        )
    )
    if order.cancel_at is not None:
        result.append(
            Fact(
                id=f"cancel_requested:{order.id}",
                type="cancel_requested",
                record_id=order.id,
                occurred_at=order.cancel_at,
                order=order_view(without_observation),
            )
        )
    match order.status:
        case "filled":
            result.append(
                Fact(
                    id=f"order_filled:{order.id}",
                    type="order_filled",
                    record_id=order.id,
                    occurred_at=order.confirmed_at,
                    market_time=order.filled_at,
                    order=order_view(order),
                )
            )
        case "cancelled" | "expired" | "terminated":
            if order.confirmed_at:
                result.append(
                    Fact(
                        id=f"order_terminal:{order.id}",
                        type="order_terminal",
                        record_id=order.id,
                        occurred_at=order.confirmed_at,
                        order=order_view(without_observation),
                    )
                )
        case "accepted" | "cancel_pending":
            pass
        case _:
            assert_never(order.status)
    return result
