"""全量筛选、北京时间日期与稳定创建时间游标。"""

import base64
import binascii
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models import DomainError, Model
from app.plan_models import Review
from app.plan_queries import PageQuery
from app.plan_reviews import context_reviews
from app.review_core_models import Market, SubjectType
from app.review_models import ReviewCounts, ReviewPage
from app.review_repository import ReviewContext


class RecordQuery(PageQuery):
    """复盘与笔记共用日期含首尾日规则，各自传入对应记录时间。"""

    round_id: str | None = None
    symbol: str | None = None
    market: Market | None = None
    start_date: date | None = None
    end_date: date | None = None


class WorkbenchQuery(RecordQuery):
    """计划生命周期与复盘状态分别筛选。"""

    plan_status: Literal["all", "draft", "active", "ended", "legacy"] = "all"
    review_state: Literal["all", "todo", "pending", "needs_update", "reviewed", "not_due"] = "all"
    cursor: str | None = None


class ReviewCursor(Model):
    """普通分页位置，仅标记轮次和不可变主体排序键。"""

    round_id: str
    created_at: datetime
    subject_type: SubjectType
    subject_id: str


def validate_dates(query: RecordQuery) -> None:
    """倒序日期明确返回422。"""
    if query.start_date and query.end_date and query.start_date > query.end_date:
        raise DomainError("date_range", "开始日期不能晚于结束日期", 422)


def in_dates(created_at: datetime, query: RecordQuery) -> bool:
    """北京时间整日判断，结束日期包含全天。"""
    day = created_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    return (query.start_date is None or day >= query.start_date) and (query.end_date is None or day <= query.end_date)


def base_matches(review: Review, query: WorkbenchQuery) -> bool:
    """状态计数之前先筛选主体。"""
    lifecycle = review.plan.status if review.plan else "legacy"
    return (
        (not query.symbol or query.symbol.casefold() in review.symbol.casefold())
        and (query.market is None or query.market == review.market)
        and query.plan_status in ("all", lifecycle)
        and in_dates(review.latest_record_at, query)
    )


def state_matches(review: Review, query: WorkbenchQuery) -> bool:
    """todo包含首次和补充，四种状态仍分别计数。"""
    return query.review_state in ("all", review.review_state) or (
        query.review_state == "todo" and review.review_state in ("pending", "needs_update")
    )


def filtered_reviews(context: ReviewContext, query: WorkbenchQuery, now: datetime) -> list[Review]:
    """导出与列表共享同一完整主体筛选。"""
    validate_dates(query)
    return [r for r in context_reviews(context, now) if base_matches(r, query) and state_matches(r, query)]


def sort_key(review: Review) -> tuple[float, str, str]:
    """最新创建在前，同时间按主体类型和ID排序。"""
    return -review.created_at.timestamp(), review.subject_type, review.id


def cursor_tail(items: list[Review], token: str | None, round_id: str) -> list[Review]:
    """请求期间换轮不能拼接；游标项目即使已被筛掉仍可定位。"""
    if token is None:
        return items
    try:
        cursor = ReviewCursor.model_validate_json(base64.b64decode(token.encode(), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError, binascii.Error, ValidationError) as error:
        raise DomainError("review_cursor", "分页位置无效，请刷新列表", 422) from error
    if cursor.round_id != round_id:
        raise DomainError("review_round_changed", "轮次已变化，请重新载入列表")
    key = (-cursor.created_at.timestamp(), cursor.subject_type, cursor.subject_id)
    return [r for r in items if sort_key(r) > key]


def workbench(context: ReviewContext, query: WorkbenchQuery, now: datetime) -> ReviewPage:
    """计数基于完整匹配主体，翻页不缩小计数。"""
    validate_dates(query)
    base = [r for r in context_reviews(context, now) if base_matches(r, query)]
    counts = ReviewCounts(
        pending=sum(r.review_state == "pending" for r in base),
        needs_update=sum(r.review_state == "needs_update" for r in base),
        reviewed=sum(r.review_state == "reviewed" for r in base),
        not_due=sum(r.review_state == "not_due" for r in base),
    )
    selected = [r for r in base if state_matches(r, query)]
    tail = cursor_tail(selected, query.cursor, context.state.id)
    items = tail[: query.limit]
    next_cursor = None
    if len(tail) > query.limit:
        last = items[-1]
        cursor = ReviewCursor(
            round_id=context.state.id, created_at=last.created_at, subject_type=last.subject_type, subject_id=last.id
        )
        next_cursor = base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode()
    return ReviewPage(
        items=items,
        total=len(selected),
        round_id=context.state.id,
        read_only=context.state.archived_at is not None,
        counts=counts,
        next_cursor=next_cursor,
        limit=query.limit,
    )
