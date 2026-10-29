"""北京时间每日首次完整观察持久化，不追填离线日期。"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.engine import Engine
from app.personal_models import SnapshotFailure, ValuationSnapshot
from app.storage import Store, read_rows
from app.valuation import value_account


def snapshots(store: Store, round_id: str) -> list[ValuationSnapshot]:
    """返回实际采集日期；缺失日期由图表保留缺口。"""
    with store.transaction() as connection:
        return [
            ValuationSnapshot.model_validate_json(row[0])
            for row in read_rows(
                connection,
                "SELECT data FROM snapshots WHERE round_id=? ORDER BY day",
                (round_id,),
            )
        ]


def capture(engine: Engine, now: datetime | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
    """事务外采集，结束时间标记观察；now仅为受控兼容入口，正式调用使用实时clock。"""
    read_time = clock or (lambda: now if now is not None else datetime.now(UTC))
    started = read_time()
    day = started.astimezone(ZoneInfo("Asia/Shanghai")).date()
    state = engine.store.read()
    with engine.store.transaction() as connection:
        rows = read_rows(connection, "SELECT data FROM snapshots WHERE round_id=? AND day=?", (state.id, str(day)))
        if rows and ValuationSnapshot.model_validate_json(rows[0][0]).status == "complete":
            return
    valuation = value_account(engine, state, started)
    completed = read_time()
    if completed.astimezone(ZoneInfo("Asia/Shanghai")).date() != day:
        return
    failure = None
    if valuation.status != "complete":
        failure = SnapshotFailure(captured_at=completed, status=valuation.status, issues=valuation.issues)
    point = ValuationSnapshot(
        id=str(uuid4()),
        round_id=state.id,
        day=day,
        captured_at=completed,
        total_assets_cny=valuation.total,
        pnl_cny=valuation.total - Decimal(1000000) if valuation.total is not None else None,
        status=valuation.status,
        issues=valuation.issues,
        price_times=valuation.times,
        first_failure=failure,
    )
    with engine.store.transaction() as connection:
        if engine.store.load(connection) != state or read_time().astimezone(ZoneInfo("Asia/Shanghai")).date() != day:
            return
        rows = read_rows(connection, "SELECT data FROM snapshots WHERE round_id=? AND day=?", (state.id, str(day)))
        if rows:
            previous = ValuationSnapshot.model_validate_json(rows[0][0])
            if previous.status == "complete" or point.status != "complete":
                return
            first_failure = previous.first_failure or SnapshotFailure(
                captured_at=previous.captured_at,
                status=previous.status,
                issues=previous.issues,
            )
            point = point.model_copy(update={"id": previous.id, "first_failure": first_failure})
            _ = connection.execute(
                "UPDATE snapshots SET data=? WHERE round_id=? AND day=?", (point.model_dump_json(), state.id, str(day))
            )
        else:
            _ = connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (state.id, str(day), point.model_dump_json()))
