"""资产快照最大比例回撤、分段边界与区间接口测试。"""

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.analysis_models import SnapshotQuery, SnapshotRange
from app.api_models import ResetInput
from app.engine import Engine
from app.operations import reset
from app.personal_models import ValuationSnapshot
from app.snapshot_queries import calculate_drawdown, snapshot_range, snapshot_segments

DAY = date(2026, 11, 20)
SnapshotStatus = Literal["complete", "stale", "missing"]


def point(
    day: date,
    amount: str | None,
    *,
    status: SnapshotStatus = "complete",
    identifier: str | None = None,
) -> ValuationSnapshot:
    """构造每日观察，金额不经过任何量化。"""
    value = Decimal(amount) if amount is not None else None
    return ValuationSnapshot(
        id=identifier if identifier is not None else f"point-{day}",
        round_id="controlled-round",
        day=day,
        captured_at=datetime(day.year, day.month, day.day, 1, tzinfo=UTC),
        total_assets_cny=value,
        pnl_cny=value,
        status=status,
        issues=[] if status == "complete" else ["受控不完整"],
        price_times={},
    )


def consecutive(*values: str | None, start: date = DAY) -> list[ValuationSnapshot]:
    return [point(start + timedelta(days=index), value) for index, value in enumerate(values)]


def save_points(engine: Engine, items: list[ValuationSnapshot]) -> None:
    """只向测试临时库写入受控快照。"""
    round_id = engine.store.read().id
    with engine.store.transaction() as connection:
        for item in items:
            saved = item.model_copy(update={"round_id": round_id})
            _ = connection.execute(
                "INSERT INTO snapshots VALUES(?,?,?)",
                (round_id, str(saved.day), saved.model_dump_json()),
            )


@pytest.mark.parametrize("values", [("100", "110", "120"), ("100", "100", "100")])
def test_rising_and_equal_values_are_no_drop(values: tuple[str, ...]) -> None:
    result = calculate_drawdown(consecutive(*values))

    assert result.status == "no_drop"
    assert result.reason == "连续完整记录中没有出现回落"
    assert result.peak is None
    assert result.trough is None
    assert result.amount_cny is None
    assert result.percent is None
    assert result.segments_used == 1
    assert result.broken_days == 0


def test_single_point_and_isolated_points_are_insufficient() -> None:
    single = calculate_drawdown(consecutive("100"))
    isolated = calculate_drawdown([point(DAY, "100"), point(DAY + timedelta(days=2), "90")])

    assert single.status == "insufficient"
    assert single.reason == "连续完整记录不足两天，无法计算回落"
    assert single.segments_used == 0
    assert single.broken_days == 0
    assert isolated.status == "insufficient"
    assert isolated.segments_used == 0
    assert isolated.broken_days == 1


def test_missing_day_prevents_cross_segment_drawdown() -> None:
    items = consecutive("100", "100") + consecutive("90", "90", start=DAY + timedelta(days=3))

    result = calculate_drawdown(items)

    assert result.status == "no_drop"
    assert result.segments_used == 2
    assert result.broken_days == 1


@pytest.mark.parametrize(("status", "amount"), [("stale", "90"), ("complete", None)])
def test_incomplete_record_prevents_cross_segment_drawdown(status: SnapshotStatus, amount: str | None) -> None:
    items = [
        *consecutive("100", "100"),
        point(DAY + timedelta(days=2), amount, status=status),
        *consecutive("90", "90", start=DAY + timedelta(days=3)),
    ]

    result = calculate_drawdown(items)

    assert result.status == "no_drop"
    assert result.segments_used == 2
    assert result.broken_days == 1


def test_peak_at_segment_tail_is_not_paired_backwards() -> None:
    """段尾最高值没有随后低点，只能使用此前正向的高点低点对。"""
    result = calculate_drawdown(consecutive("100", "80", "120"))

    assert result.status == "computed"
    assert result.peak is not None
    assert result.peak.day == DAY
    assert result.trough is not None
    assert result.trough.day == DAY + timedelta(days=1)
    assert result.amount_cny == Decimal(20)
    assert result.percent == Decimal("20.00")


def test_equal_peak_values_choose_earliest_peak() -> None:
    result = calculate_drawdown(consecutive("100", "80", "100", "50"))

    assert result.status == "computed"
    assert result.peak is not None
    assert result.peak.day == DAY
    assert result.trough is not None
    assert result.trough.day == DAY + timedelta(days=3)
    assert result.amount_cny == Decimal(50)
    assert result.percent == Decimal("50.00")


def test_equal_ratios_choose_earlier_trough() -> None:
    result = calculate_drawdown(consecutive("100", "50", "200", "100"))

    assert result.status == "computed"
    assert result.peak is not None
    assert result.peak.day == DAY
    assert result.trough is not None
    assert result.trough.day == DAY + timedelta(days=1)
    assert result.amount_cny == Decimal(50)
    assert result.percent == Decimal("50.00")


def test_exact_ratio_comparison_precedes_half_up_output_rounding() -> None:
    """两组都显示 2.35%，但未舍入的 2.3451% 必须胜过 2.3450%。"""
    items = consecutive("10000000", "9765500") + consecutive("100000000", "97654900", start=DAY + timedelta(days=3))

    result = calculate_drawdown(items)

    assert result.status == "computed"
    assert result.peak is not None
    assert result.peak.day == DAY + timedelta(days=3)
    assert result.trough is not None
    assert result.trough.day == DAY + timedelta(days=4)
    assert result.amount_cny == Decimal(2345100)
    assert result.percent == Decimal("2.35")
    assert result.reason == "从 11-23 的高点回落到 11-24 的低点"
    assert result.segments_used == 2
    assert result.broken_days == 1


def test_non_positive_peaks_do_not_participate() -> None:
    unknown = calculate_drawdown(consecutive("0", "-100", "-200"))
    later_positive = calculate_drawdown(consecutive("0", "-100", "100", "50"))

    assert unknown.status == "no_drop"
    assert unknown.amount_cny is None
    assert unknown.percent is None
    assert later_positive.status == "computed"
    assert later_positive.peak is not None
    assert later_positive.peak.day == DAY + timedelta(days=2)
    assert later_positive.trough is not None
    assert later_positive.trough.day == DAY + timedelta(days=3)
    assert later_positive.amount_cny == Decimal(50)
    assert later_positive.percent == Decimal("50.00")


def test_segments_match_frontend_rules_on_unsorted_boundary_data() -> None:
    """乱序、缺日、非完整及空金额的记录边界与前端 snapshotSegments 相同。"""
    items = [
        point(DAY, "100", identifier="A"),
        point(DAY + timedelta(days=1), "100", identifier="B"),
        point(DAY + timedelta(days=3), "90", identifier="C"),
        point(DAY + timedelta(days=4), "90", status="stale", identifier="D"),
        point(DAY + timedelta(days=5), None, identifier="E"),
        point(DAY + timedelta(days=6), "80", identifier="F"),
        point(DAY + timedelta(days=7), "80", identifier="G"),
        point(DAY + timedelta(days=9), "70", identifier="H"),
    ]

    segments = snapshot_segments(list(reversed(items)))
    result = calculate_drawdown(list(reversed(items)))

    assert [[item.id for item in segment] for segment in segments] == [["A", "B"], ["C"], ["F", "G"], ["H"]]
    assert result.segments_used == 2
    assert result.broken_days == 4


def test_broken_days_exclude_dates_before_first_and_today(engine: Engine) -> None:
    """查询范围前后的缺日仍计入 missing_days，但不计入 broken_days。"""
    save_points(engine, consecutive("100", "90", start=date(2026, 11, 21)))
    query = SnapshotQuery(range="custom", start_date=date(2026, 11, 19), end_date=date(2026, 11, 25))

    result = snapshot_range(
        engine.store,
        engine.store.read(),
        query,
        datetime(2026, 11, 25, 4, tzinfo=UTC),
    )

    assert result.missing_days == 5
    assert result.drawdown.broken_days == 0
    assert result.drawdown.status == "computed"
    assert result.drawdown.amount_cny == Decimal(10)


def test_current_and_archived_routes_include_drawdown_without_csv_change(engine: Engine, api: TestClient) -> None:
    """当前轮与归档轮共用计算，CSV 列保持原样。"""
    items = consecutive("100", "90", "95", start=date(2026, 10, 22))
    save_points(engine, items)
    old_round_id = engine.store.read().id
    suffix = "range=custom&start_date=2026-10-22&end_date=2026-10-24"

    current_response = api.get(f"/api/snapshots/range?{suffix}")

    assert current_response.status_code == 200
    current = SnapshotRange.model_validate_json(current_response.content)
    assert current_response.json()["drawdown"]["percent"] == "10.00"
    assert current.drawdown.status == "computed"
    assert current.drawdown.peak is not None
    assert current.drawdown.peak.day == date(2026, 10, 22)
    assert current.drawdown.peak.captured_at == items[0].captured_at
    assert current.drawdown.peak.total_assets_cny == Decimal(100)
    assert current.drawdown.trough is not None
    assert current.drawdown.trough.day == date(2026, 10, 23)
    assert current.drawdown.trough.captured_at == items[1].captured_at
    assert current.drawdown.trough.total_assets_cny == Decimal(90)
    assert current.drawdown.amount_cny == Decimal(10)
    assert current.drawdown.percent == Decimal("10.00")
    assert current.drawdown.reason == "从 10-22 的高点回落到 10-23 的低点"
    assert current.drawdown.segments_used == 1
    assert current.drawdown.broken_days == 0

    _ = reset(
        engine,
        ResetInput(request_id=str(uuid4()), confirmation="重新开始"),
        datetime(2026, 10, 24, 4, tzinfo=UTC),
    )
    archived_response = api.get(f"/api/rounds/{old_round_id}/snapshots/range?{suffix}")
    exported = api.get(f"/api/rounds/{old_round_id}/snapshots/export.csv?{suffix}")

    assert archived_response.status_code == 200
    archived = SnapshotRange.model_validate_json(archived_response.content)
    assert archived.drawdown == current.drawdown
    assert exported.status_code == 200
    rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert rows[0] == [
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
    assert len(rows) == 4
