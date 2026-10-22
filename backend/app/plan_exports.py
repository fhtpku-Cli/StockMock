"""完整计划和复盘导出，用户文本在 CSV 单元格中按纯文本保存。"""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import assert_never

from fastapi import Response

from app.models import State
from app.plan_models import Alert, Plan
from app.plan_reviews import reviews
from app.storage import Store


def safe_cell(value: str | Decimal | int | None) -> str:
    """阻断前导空白后的公式及制表符、回车开头的电子表格公式。"""
    match value:
        case None:
            return "未记录"
        case int() | Decimal():
            return str(value)
        case str():
            text = value
        case _:
            assert_never(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def csv_response(output: io.StringIO, filename: str) -> Response:
    """带 BOM 的 UTF-8 可直接在中文电子表格中查看。"""
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def export_plans(plans: list[Plan]) -> Response:
    """计划导出不受页面分页限制。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "计划编号",
            "轮次",
            "证券",
            "币种",
            "方向",
            "状态",
            "版本",
            "理由",
            "目标参考价",
            "止损参考价",
            "复盘日期（北京时间）",
            "失效条件",
            "创建时间",
            "修改时间",
        ]
    )
    for plan in plans:
        writer.writerow(
            [
                safe_cell(value)
                for value in (
                    plan.id,
                    plan.round_id,
                    plan.symbol,
                    plan.currency,
                    plan.direction,
                    plan.status,
                    plan.version,
                    plan.reason or None,
                    plan.target_price,
                    plan.stop_price,
                    plan.review_date.isoformat() if plan.review_date else None,
                    plan.invalidation or None,
                    plan.created_at.isoformat(),
                    plan.updated_at.isoformat(),
                )
            ]
        )
    return csv_response(output, "plans.csv")


def export_reviews(store: Store, state: State) -> Response:
    """每笔委托保留当时快照、成交时间和来源；无委托计划及旧独立笔记同样导出。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
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
        ]
    )
    for review in reviews(store, state, datetime.now(UTC)):
        orders = [o for o in state.orders if o.plan_id == review.plan.id] if review.plan else [review.legacy_order]
        for order in orders or [None]:
            writer.writerow(
                [
                    safe_cell(value)
                    for value in (
                        state.id,
                        review.plan.id if review.plan else None,
                        review.status,
                        review.actual_status,
                        order.id if order else None,
                        review.plan.symbol if review.plan else order.symbol if order else None,
                        review.currency,
                        order.side if order else None,
                        order.status if order else None,
                        order.quantity if order and order.status == "filled" else 0,
                        order.fill_price if order else None,
                        order.fee if order else None,
                        order.realized_pnl if order else None,
                        order.filled_at.isoformat() if order and order.filled_at else None,
                        order.confirmed_at.isoformat() if order and order.confirmed_at else None,
                        order.source if order else None,
                        order.plan_snapshot.model_dump_json() if order and order.plan_snapshot else None,
                        order.reason if order else None,
                        "\n".join(f"{note.created_at.isoformat()} {note.text}" for note in review.notes) or None,
                    )
                ]
            )
    for note in state.journal:
        if note.order_id is None and note.plan_id is None:
            writer.writerow(
                [
                    safe_cell(value)
                    for value in (
                        state.id,
                        None,
                        "独立旧笔记",
                        *([None] * 15),
                        f"{note.created_at.isoformat()} {note.text}",
                    )
                ]
            )
    return csv_response(output, "reviews.csv")


def export_alerts(items: list[Alert]) -> Response:
    """保留提示实际样本时间、来源和已读时间，归档完整可导出。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "提示编号",
            "轮次",
            "计划编号",
            "计划版本",
            "证券",
            "条件",
            "说明",
            "产生时间",
            "市场样本时间",
            "样本价格",
            "来源",
            "已读时间",
        ]
    )
    for alert in items:
        writer.writerow(
            [
                safe_cell(value)
                for value in (
                    alert.id,
                    alert.round_id,
                    alert.plan_id,
                    alert.plan_version,
                    alert.symbol,
                    alert.condition,
                    alert.message,
                    alert.created_at.isoformat(),
                    alert.market_time.isoformat() if alert.market_time else None,
                    alert.price,
                    alert.source,
                    alert.read_at.isoformat() if alert.read_at else None,
                )
            ]
        )
    return csv_response(output, "alerts.csv")
