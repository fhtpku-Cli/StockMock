"""实际快照区间摘要和导出，不更新或补造历史。"""

import csv
import io
import json
from datetime import datetime, timedelta
from typing import assert_never

from fastapi import Response

from app.analysis_models import SnapshotQuery, SnapshotRange
from app.cash_queries import BEIJING
from app.models import State
from app.plan_exports import safe_cell
from app.snapshots import snapshots
from app.storage import Store


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
    )


def snapshot_csv(result: SnapshotRange) -> Response:
    """保存的金额缺失时留空，来源时间不重新拉取。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["轮次", "北京时间记录日", "实际采集时间", "人民币总资产", "本轮收益", "状态", "问题", "行情及汇率时间"]
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
            ]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="snapshots.csv"'},
    )
