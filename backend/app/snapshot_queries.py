"""实际快照区间摘要和导出，不更新或补造历史。"""

import csv
import io
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import assert_never

from fastapi import Response

from app.analysis_models import DrawdownPoint, SnapshotDrawdown, SnapshotQuery, SnapshotRange
from app.cash_queries import BEIJING
from app.composition import percentage, rounded
from app.models import State
from app.personal_models import ValuationSnapshot
from app.plan_exports import safe_cell
from app.snapshots import snapshots
from app.storage import Store

MIN_SEGMENT_LENGTH = 2


def snapshot_segments(items: list[ValuationSnapshot]) -> list[list[ValuationSnapshot]]:
    """与前端 snapshotSegments 一致：无效记录和日期缺口都断开。"""
    segments: list[list[ValuationSnapshot]] = []
    current: list[ValuationSnapshot] = []
    for point in sorted(items, key=lambda item: item.day):
        previous = current[-1] if current else None
        if point.status != "complete" or point.total_assets_cny is None:
            current = []
            continue
        if previous is None or point.day - previous.day != timedelta(days=1):
            current = []
            segments.append(current)
        current.append(point)
    return segments


def broken_days(items: list[ValuationSnapshot]) -> int:
    """仅计算实际首末记录所夹日期内的缺日与不完整日。"""
    if not items:
        return 0
    ordered = sorted(items, key=lambda item: item.day)
    complete_days = {point.day for point in items if point.status == "complete" and point.total_assets_cny is not None}
    span = (ordered[-1].day - ordered[0].day).days + 1
    return sum(ordered[0].day + timedelta(days=offset) not in complete_days for offset in range(span))


def drawdown_point(point: ValuationSnapshot, value: Decimal) -> DrawdownPoint:
    """保留实际记录时间和未量化金额。"""
    return DrawdownPoint(day=point.day, captured_at=point.captured_at, total_assets_cny=value)


def best_drawdown_pair(
    segments: list[list[ValuationSnapshot]],
) -> tuple[Decimal, ValuationSnapshot, ValuationSnapshot] | None:
    """逐对比较精确比例，并依次按低点、高点日期解决并列。"""
    best: tuple[Decimal, ValuationSnapshot, ValuationSnapshot] | None = None
    for segment in segments:
        for peak_index, peak in enumerate(segment[:-1]):
            peak_value = peak.total_assets_cny
            if peak_value is None:
                continue
            for trough in segment[peak_index + 1 :]:
                trough_value = trough.total_assets_cny
                if trough_value is None:
                    continue
                ratio = percentage(peak_value - trough_value, peak_value)
                if ratio is None or ratio <= 0:
                    continue
                if (
                    best is None
                    or ratio > best[0]
                    or (ratio == best[0] and (trough.day, peak.day) < (best[2].day, best[1].day))
                ):
                    best = (ratio, peak, trough)
    return best


def calculate_drawdown(items: list[ValuationSnapshot]) -> SnapshotDrawdown:
    """用未舍入比例选择最大回落，再按低点及高点日期处理并列。"""
    segments = [segment for segment in snapshot_segments(items) if len(segment) >= MIN_SEGMENT_LENGTH]
    best = best_drawdown_pair(segments)
    gaps = broken_days(items)
    if best is None:
        if not segments:
            return SnapshotDrawdown(
                status="insufficient",
                reason="连续完整记录不足两天，无法计算回落",
                peak=None,
                trough=None,
                amount_cny=None,
                percent=None,
                segments_used=0,
                broken_days=gaps,
            )
        return SnapshotDrawdown(
            status="no_drop",
            reason="连续完整记录中没有出现回落",
            peak=None,
            trough=None,
            amount_cny=None,
            percent=None,
            segments_used=len(segments),
            broken_days=gaps,
        )
    ratio, peak, trough = best
    peak_value = peak.total_assets_cny
    trough_value = trough.total_assets_cny
    if peak_value is None or trough_value is None:
        raise ValueError("完整回撤端点缺少资产金额")
    return SnapshotDrawdown(
        status="computed",
        reason=f"从 {peak.day:%m-%d} 的高点回落到 {trough.day:%m-%d} 的低点",
        peak=drawdown_point(peak, peak_value),
        trough=drawdown_point(trough, trough_value),
        amount_cny=peak_value - trough_value,
        percent=rounded(ratio),
        segments_used=len(segments),
        broken_days=gaps,
    )


def snapshot_range(store: Store, state: State, query: SnapshotQuery, now: datetime) -> SnapshotRange:
    """仅实际首末端点均完整且不同日时相减。"""
    end = (state.archived_at or now).astimezone(BEIJING).date()
    start = state.created_at.astimezone(BEIJING).date()
    match query.range:
        case "all":
            pass
        case "7d" | "30d" | "90d":
            start = end - timedelta(days=int(query.range[:-1]) - 1)
        case "custom":
            if query.start_date is not None and query.end_date is not None:
                start, end = query.start_date, query.end_date
        case _:
            assert_never(query.range)
    items = [point for point in snapshots(store, state.id) if start <= point.day <= end]
    first = items[0] if items else None
    last = items[-1] if items else None
    complete = sum(point.status == "complete" and point.total_assets_cny is not None for point in items)
    missing = max(0, (end - start).days + 1 - len(items))
    continuous = missing == 0 and complete == len(items) and bool(items)
    change = None
    reason = "没有实际记录"
    if first is not None and last is not None:
        reason = "只有一个记录日，不能计算首末变化"
        if first.day != last.day:
            reason = "实际首末记录估值不完整"
            if (
                first.status == last.status == "complete"
                and first.total_assets_cny is not None
                and last.total_assets_cny is not None
            ):
                change = last.total_assets_cny - first.total_assets_cny
                reason = "已记录首末资产变化" if continuous else "已记录首末资产变化；覆盖不连续或含不完整估值"
    return SnapshotRange(
        round_id=state.id,
        start_date=start,
        end_date=end,
        items=items,
        recorded_days=len(items),
        complete_days=complete,
        missing_days=missing,
        first=first,
        last=last,
        change_cny=change,
        continuous=continuous,
        reason=reason,
        drawdown=calculate_drawdown(items),
    )


def snapshot_csv(result: SnapshotRange) -> Response:
    """保存的金额缺失时留空，来源时间不重新拉取。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "轮次",
            "北京时间记录日",
            "实际采集时间",
            "人民币总资产",
            "本轮收益",
            "状态",
            "问题",
            "行情及汇率时间",
            "首次失败时间",
            "首次失败状态",
            "首次失败原因",
        ]
    )
    for point in result.items:
        writer.writerow(
            [
                point.round_id,
                point.day,
                point.captured_at.isoformat(),
                point.total_assets_cny if point.total_assets_cny is not None else "",
                point.pnl_cny if point.pnl_cny is not None else "",
                point.status,
                safe_cell("；".join(point.issues)),
                json.dumps({key: value.isoformat() for key, value in point.price_times.items()}, ensure_ascii=False),
                point.first_failure.captured_at.isoformat() if point.first_failure else "",
                point.first_failure.status if point.first_failure else "",
                safe_cell("；".join(point.first_failure.issues)) if point.first_failure else "",
            ]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="snapshots.csv"'},
    )
