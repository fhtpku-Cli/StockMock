"""关联委托、实际成交和笔记的复盘汇总，不改变收益计算。"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models import DomainError, Journal, Order, State
from app.plan_models import Plan, Review
from app.plans import ensure_current, round_plans
from app.storage import Store


def summarize(state: State, plan: Plan | None, orders: list[Order], now: datetime) -> Review:
    """仅汇总已经确认的卖出净收益，剩余数量不视为已结束。"""
    symbol = plan.symbol if plan else orders[0].symbol
    order_ids = {o.id for o in orders}
    notes = [n for n in state.journal if n.order_id in order_ids or (plan is not None and n.plan_id == plan.id)]
    fills = [o for o in orders if o.status == "filled"]
    bought = sum(o.quantity for o in fills if o.side == "buy")
    sold = sum(o.quantity for o in fills if o.side == "sell")
    sales = [o for o in fills if o.side == "sell"]
    profits = [o.realized_pnl for o in sales if o.realized_pnl is not None]
    due = bool(plan and plan.review_date and plan.review_date <= now.astimezone(ZoneInfo("Asia/Shanghai")).date())
    account_quantity = sum(lot.quantity for lot in state.lots if lot.symbol == symbol)
    status = "reviewed" if notes else "pending" if fills or due else "not_due"
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
    )


def reviews(store: Store, state: State, now: datetime) -> list[Review]:
    """旧单不推断为计划，保留独立复盘入口。"""
    plans = round_plans(store, state.id)
    grouped: dict[str, list[Order]] = {plan.id: [] for plan in plans}
    legacy: list[Order] = []
    for order in state.orders:
        if order.plan_id in grouped:
            grouped[order.plan_id].append(order)
        else:
            legacy.append(order)
    return [summarize(state, plan, grouped[plan.id], now) for plan in plans] + [
        summarize(state, None, [order], now) for order in reversed(legacy)
    ]


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
