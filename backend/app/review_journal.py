"""普通笔记完整筛选与可靠归属，不改写原笔记。"""

from typing import TYPE_CHECKING

from app.models import Journal
from app.plan_queries import page

if TYPE_CHECKING:
    from app.review_core_models import SubjectType
from app.review_models import JournalItem, JournalPage
from app.review_queries import RecordQuery, in_dates, validate_dates
from app.review_repository import ReviewContext


def journal_item(context: ReviewContext, note: Journal) -> JournalItem:
    """双关联须一致，关联丢失时明确未知。"""
    plan = next((p for p in context.plans if p.id == note.plan_id), None)
    order = next((o for o in context.state.orders if o.id == note.order_id), None)
    valid = not (
        (note.plan_id and plan is None)
        or (note.order_id and order is None)
        or (plan and order and (order.plan_id != plan.id or order.symbol != plan.symbol))
    )
    subject_type: SubjectType | None = None
    subject_id: str | None = None
    symbol: str | None = None
    if valid:
        if plan is not None:
            symbol, subject_type, subject_id = plan.symbol, "plan", plan.id
        elif order is not None:
            linked = next((p for p in context.plans if p.id == order.plan_id), None)
            if order.plan_id is None:
                symbol, subject_type, subject_id = order.symbol, "order", order.id
            elif linked and linked.symbol == order.symbol:
                symbol, subject_type, subject_id = linked.symbol, "plan", linked.id
    return JournalItem(
        id=note.id,
        order_id=note.order_id,
        plan_id=note.plan_id,
        text=note.text,
        created_at=note.created_at,
        symbol=symbol,
        market=context.markets.get(symbol) if symbol else None,
        review_subject_type=subject_type,
        review_subject_id=subject_id,
    )


def journal_page(context: ReviewContext, query: RecordQuery) -> JournalPage:
    """对完整笔记应用独立的笔记日期条件，然后分页。"""
    validate_dates(query)
    notes = [journal_item(context, note) for note in context.state.journal]
    selected = [
        n
        for n in notes
        if (not query.symbol or (n.symbol is not None and query.symbol.casefold() in n.symbol.casefold()))
        and (query.market is None or n.market == query.market)
        and in_dates(n.created_at, query)
    ]
    selected.sort(key=lambda n: (-n.created_at.timestamp(), n.id))
    result = page(selected, query)
    return JournalPage(
        items=result.items,
        total=result.total,
        offset=result.offset,
        limit=result.limit,
        round_id=context.state.id,
        read_only=context.state.archived_at is not None,
    )
