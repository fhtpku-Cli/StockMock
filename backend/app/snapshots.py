"""北京时间每日首次真实观察持久化，不追填离线日期。"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.engine import Engine
from app.personal_models import ValuationSnapshot
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


def capture(engine: Engine, now: datetime) -> None:
    """行情采集在事务外，余额发生变化时放弃本次并留待下次重试。"""
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    state = engine.store.read()
    with engine.store.transaction() as connection:
        if read_rows(connection, "SELECT day FROM snapshots WHERE round_id=? AND day=?", (state.id, str(day))):
            return
    valuation = value_account(engine, state, now)
    point = ValuationSnapshot(
        id=str(uuid4()),
        round_id=state.id,
        day=day,
        captured_at=now,
        total_assets_cny=valuation.total,
        pnl_cny=valuation.total - Decimal(1000000) if valuation.total is not None else None,
        status=valuation.status,
        issues=valuation.issues,
        price_times=valuation.times,
    )
    with engine.store.transaction() as connection:
        if engine.store.load(connection) != state:
            return
        _ = connection.execute(
            "INSERT OR IGNORE INTO snapshots VALUES(?,?,?)", (state.id, str(day), point.model_dump_json())
        )
