"""同一 SQLite 连接内读取复盘事实，避免嵌套写事务。"""

import sqlite3
from dataclasses import dataclass

from app.catalog import CATALOG
from app.discovery_models import ObservationInstrument
from app.instrument_rules import InstrumentRule
from app.models import DomainError, State
from app.plan_models import Alert, Plan
from app.plans import load_plan
from app.review_core_models import Conclusion, Market
from app.storage import Store, read_rows


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """一次一致读取的轮次与独立表内容。"""

    state: State
    plans: list[Plan]
    alerts: list[Alert]
    conclusions: list[Conclusion]
    markets: dict[str, Market]


def load_context(connection: sqlite3.Connection, store: Store, round_id: str | None) -> ReviewContext:
    """所有内容使用调用者事务，不做网络请求。"""
    if round_id is None:
        state = store.load(connection)
    else:
        rows = read_rows(connection, "SELECT data FROM rounds WHERE id=?", (round_id,))
        if not rows:
            raise DomainError("round_missing", "练习轮次不存在", 404)
        state = State.model_validate_json(rows[0][0])
    plans = [
        load_plan(connection, row[0])
        for row in read_rows(connection, "SELECT id FROM plans WHERE round_id=? ORDER BY rowid", (state.id,))
    ]
    alerts = [
        Alert.model_validate_json(row[0])
        for row in read_rows(connection, "SELECT data FROM plan_alerts WHERE round_id=?", (state.id,))
    ]
    conclusions = [
        Conclusion.model_validate_json(row[0])
        for row in read_rows(
            connection, "SELECT data FROM review_conclusions WHERE round_id=? ORDER BY rowid", (state.id,)
        )
    ]
    markets: dict[str, Market] = {item.symbol: item.market for item in CATALOG}
    for row in read_rows(connection, "SELECT data FROM observations"):
        observed = ObservationInstrument.model_validate_json(row[0])
        markets[observed.symbol] = observed.market
    for row in read_rows(connection, "SELECT data FROM activated_instruments"):
        activated = InstrumentRule.model_validate_json(row[0])
        markets[activated.symbol] = activated.market
    return ReviewContext(state, plans, alerts, conclusions, markets)


def read_context(store: Store, round_id: str | None = None) -> ReviewContext:
    """面向 HTTP 的一致读取入口。"""
    with store.transaction() as connection:
        return load_context(connection, store, round_id)
