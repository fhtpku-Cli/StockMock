"""完整计划和复盘导出，用户文本在 CSV 单元格中按纯文本保存。"""

import csv
import io
from decimal import Decimal
from typing import assert_never

from fastapi import Response

from app.plan_models import Alert, Plan


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
