"""按命中主体导出完整事实，保留旧列与防公式注入处理。"""

import csv
import io
from datetime import datetime
from decimal import Decimal
from typing import Final

from fastapi import Response

from app.plan_exports import csv_response, safe_cell
from app.plan_models import Review
from app.review_conclusions import detail
from app.review_facts import detail_facts
from app.review_models import Fact
from app.review_queries import WorkbenchQuery, filtered_reviews
from app.review_repository import ReviewContext

HEADERS: Final = [
    "轮次",
    "计划编号",
    "复盘状态",
    "实际情况",
    "委托编号",
    "证券",
    "币种",
    "方向",
    "委托状态",
    "成交数量",
    "成交价格",
    "费用",
    "已实现原币净收益（仅该卖单）",
    "市场成交时间",
    "确认时间",
    "行情来源",
    "委托当时计划快照",
    "旧委托理由",
    "复盘笔记",
    "记录类型",
    "主体类型",
    "主体ID",
    "计划版本",
    "覆盖事实",
    "结论",
    "偏差",
    "下次行动",
    "记录时间",
    "事实JSON",
]


def fact_version(fact: Fact) -> int | None:
    """只使用该事实实际保存的版本，不拿当前计划补历史。"""
    if fact.alert:
        return fact.alert.plan_version
    if fact.order:
        return fact.order.plan_version
    if fact.conclusion:
        return fact.conclusion.coverage.plan_version
    return fact.plan.version if fact.plan else None


def event_cells(round_id: str, review: Review, fact: Fact) -> list[str | Decimal | int | None]:
    """旧列索引保持不变，缺值为空义而不补零。"""
    order = fact.order
    conclusion = fact.conclusion
    return [
        round_id,
        review.plan.id if review.plan else None,
        review.status,
        review.actual_status,
        order.id if order else None,
        review.symbol,
        review.currency,
        order.side if order else None,
        order.status if order else None,
        order.quantity if order and order.status == "filled" else None,
        order.fill_price if order else None,
        order.fee if order else None,
        order.realized_pnl if order else None,
        order.filled_at.isoformat() if order and order.filled_at else None,
        order.confirmed_at.isoformat() if order and order.confirmed_at else None,
        order.source if order else fact.alert.source if fact.alert else None,
        order.plan_snapshot.model_dump_json() if order and order.plan_snapshot else None,
        order.reason if order else None,
        fact.note.text if fact.note else None,
        fact.type,
        review.subject_type,
        review.id,
        fact_version(fact),
        conclusion.coverage.model_dump_json() if conclusion else None,
        conclusion.conclusion if conclusion else None,
        conclusion.deviation if conclusion else None,
        conclusion.next_action if conclusion else None,
        fact.occurred_at.isoformat() if fact.occurred_at else None,
        fact.model_dump_json(exclude={"order": {"waiting"}}),
    ]


def unfiltered(query: WorkbenchQuery) -> bool:
    """无主体条件时保留旧的独立笔记导出行为。"""
    return (
        query.symbol is None
        and query.market is None
        and query.start_date is None
        and query.end_date is None
        and query.plan_status == "all"
        and query.review_state == "all"
    )


def export_reviews(context: ReviewContext, query: WorkbenchQuery, now: datetime) -> Response:
    """日期只筛主体，主体内事实完整；limit和cursor不影响下载。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    exported_notes: set[str] = set()
    for review in filtered_reviews(context, query, now):
        loaded = detail(context, (review.subject_type, review.id), now)
        exported_notes.update(note.id for note in loaded.review.notes)
        for fact in detail_facts(context, loaded):
            writer.writerow([safe_cell(value) for value in event_cells(context.state.id, review, fact)])
    if unfiltered(query):
        for note in context.state.journal:
            if note.id not in exported_notes:
                cells: list[str | Decimal | int | None] = [None] * len(HEADERS)
                cells[0], cells[18] = context.state.id, note.text
                cells[19] = "independent_note" if note.plan_id is None and note.order_id is None else "unresolved_note"
                cells[27], cells[28] = note.created_at.isoformat(), note.model_dump_json()
                writer.writerow([safe_cell(value) for value in cells])
    return csv_response(output, "reviews.csv")
