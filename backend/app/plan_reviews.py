"""关联委托、实际成交和笔记的复盘汇总，不改变收益计算。"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models import DomainError, Journal, Order, State
from app.plan_models import Plan, Review
from app.plans import ensure_current
from app.review_core_models import Coverage
from app.review_repository import ReviewContext, read_context
from app.storage import Store


def summarize(state: State, plan: Plan | None, orders: list[Order], now: datetime) -> Review:
    """仅汇总已经确认的卖出净收益，剩余数量不视为已结束。"""
    symbol = plan.symbol if plan else orders[0].symbol
    order_ids = {o.id for o in orders}
    notes = [
        n
        for n in state.journal
        if (n.plan_id is None and n.order_id in order_ids)
        or (plan is not None and n.plan_id == plan.id and (n.order_id is None or n.order_id in order_ids))
    ]
    fills = [o for o in orders if o.status == "filled"]
    bought = sum(o.quantity for o in fills if o.side == "buy")
    sold = sum(o.quantity for o in fills if o.side == "sell")
    sales = [o for o in fills if o.side == "sell"]
    profits = [o.realized_pnl for o in sales if o.realized_pnl is not None]
    due = bool(plan and plan.review_date and plan.review_date <= now.astimezone(ZoneInfo("Asia/Shanghai")).date())
    account_quantity = sum(lot.quantity for lot in state.lots if lot.symbol == symbol)
    status = "pending" if fills or due else "not_due"
    descriptions: list[str] = []
    if plan and plan.status == "ended":
        descriptions.append("计划已由本人结束")
    if fills:
        descriptions.append(f"已成交 {len(fills)} 笔")
        if sold and account_quantity:
            descriptions.append("部分卖出后仍持仓；已实现收益不代表整个计划最终收益")
        elif account_quantity:
            descriptions.append("本轮该证券仍持仓")
    elif orders:
        descriptions.append("尚无成交；撤单判断不计作盈利交易")
    else:
        descriptions.append("尚无关联委托")
    if due:
        descriptions.append("已到复盘日期（北京时间）")
    return Review(
        id=plan.id if plan else orders[0].id,
        plan=plan,
        legacy_order=None if plan else orders[0],
        notes=notes,
        order_count=len(orders),
        filled_count=len(fills),
        bought_quantity=bought,
        sold_quantity=sold,
        remaining_quantity=bought - sold,
        account_quantity=account_quantity,
        realized_pnl=sum(profits, Decimal(0)) if profits and len(profits) == len(sales) else None,
        currency=plan.currency if plan else orders[0].currency,
        status=status,
        actual_status="；".join(descriptions),
        subject_type="plan" if plan else "order",
        symbol=symbol,
        created_at=plan.created_at if plan else orders[0].created_at,
        latest_record_at=max(
            [plan.updated_at if plan else orders[0].created_at]
            + [o.created_at for o in orders]
            + [o.confirmed_at for o in orders if o.confirmed_at]
            + [n.created_at for n in notes]
        ),
        review_state=status,
        update_reasons=[],
        coverage=Coverage(
            plan_version=plan.version if plan else None,
            filled_order_ids=sorted(o.id for o in fills),
            due_review_date=plan.review_date if plan and due else None,
        ),
    )


def context_reviews(context: ReviewContext, now: datetime) -> list[Review]:
    """基于同一读取计算全部状态，归档固定使用归档时刻。"""
    state = context.state
    at = state.archived_at or now
    grouped: dict[str, list[Order]] = {plan.id: [] for plan in context.plans}
    legacy: list[Order] = []
    for order in state.orders:
        if order.plan_id in grouped:
            grouped[order.plan_id].append(order)
        elif order.plan_id is None:
            legacy.append(order)
    summaries = [summarize(state, plan, grouped[plan.id], at) for plan in context.plans] + [
        summarize(state, None, [order], at) for order in legacy
    ]
    result: list[Review] = []
    for summary in summaries:
        conclusions = [
            c for c in context.conclusions if c.subject_type == summary.subject_type and c.subject_id == summary.id
        ]
        latest = conclusions[-1] if conclusions else None
        coverage = summary.coverage.model_copy(update={"latest_conclusion_id": latest.id if latest else None})
        reasons: list[str] = []
        if latest:
            if latest.coverage.plan_version != coverage.plan_version:
                reasons.append("计划版本已变化")
            if set(latest.coverage.filled_order_ids) != set(coverage.filled_order_ids):
                reasons.append("有新增已确认成交")
            if latest.coverage.due_review_date != coverage.due_review_date:
                reasons.append("已到新的约定复盘日期")
        review_state = ("needs_update" if reasons else "reviewed") if latest else summary.review_state
        recent = (
            [summary.latest_record_at]
            + [c.created_at for c in conclusions]
            + [a.created_at for a in context.alerts if summary.plan and a.plan_id == summary.id]
        )
        result.append(
            summary.model_copy(
                update={
                    "coverage": coverage,
                    "update_reasons": reasons,
                    "review_state": review_state,
                    "status": "pending" if review_state == "needs_update" else review_state,
                    "latest_record_at": max(recent),
                    "market": context.markets.get(summary.symbol),
                }
            )
        )
    return sorted(result, key=lambda r: (-r.created_at.timestamp(), r.subject_type, r.id))


def reviews(store: Store, state: State, now: datetime) -> list[Review]:
    """兼容现有调用方，同时在新事务重新读取完整一致事实。"""
    return context_reviews(read_context(store, state.id), now)


def attach_note(state: State, note: Journal, plan: Plan | None) -> None:
    """笔记可记录撤单判断；同时关联计划和订单时必须属于同一计划。"""
    order = next((o for o in state.orders if o.id == note.order_id), None)
    if note.order_id is not None and order is None:
        raise DomainError("order_missing", "关联委托不存在", 404)
    if plan is not None:
        ensure_current(state, plan)
        if order is not None and order.plan_id != plan.id:
            raise DomainError("plan_order_mismatch", "计划与关联委托不匹配", 422)
    state.journal.append(note)
