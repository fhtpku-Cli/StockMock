"""计划持久事务、并发版本和订单快照校验。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.discovery_models import ObservationInstrument
from app.models import DomainError, Instrument, OrderInput, PlanSnapshot, State
from app.plan_models import Monitoring, Plan, PlanInput, PlanUpdate
from app.storage import Store, read_rows


class PlanContext(Protocol):
    """计划只依赖存储和本地目录，不依赖交易引擎实现。"""

    store: Store

    @property
    def catalog(self) -> tuple[Instrument, ...]:
        """返回已知证券。"""
        ...


def load_plan(connection: sqlite3.Connection, plan_id: str) -> Plan:
    """读取计划及同一修订的观察状态，不混入旧条件结果。"""
    rows = read_rows(connection, "SELECT data FROM plans WHERE id=?", (plan_id,))
    if not rows:
        raise DomainError("plan_missing", "计划不存在", 404)
    plan = Plan.model_validate_json(rows[0][0])
    checks = read_rows(
        connection, "SELECT data FROM plan_monitoring WHERE plan_id=? AND version=?", (plan.id, str(plan.version))
    )
    return plan.model_copy(update={"monitoring": Monitoring.model_validate_json(checks[0][0])}) if checks else plan


def get_plan(store: Store, plan_id: str) -> Plan:
    """当前和归档都允许读取。"""
    with store.transaction() as connection:
        return load_plan(connection, plan_id)


def round_plans(store: Store, round_id: str) -> list[Plan]:
    """按最新创建顺序读取轮次计划。"""
    with store.transaction() as connection:
        ids = read_rows(connection, "SELECT id FROM plans WHERE round_id=? ORDER BY rowid DESC", (round_id,))
        return [load_plan(connection, row[0]) for row in ids]


def save_plan(connection: sqlite3.Connection, plan: Plan) -> None:
    """唯一索引在并发创建和激活时保留同证券仅一个活动计划。"""
    try:
        _ = connection.execute(
            """INSERT INTO plans VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,version=excluded.version,data=excluded.data""",
            (
                plan.id,
                plan.round_id,
                plan.symbol,
                plan.status,
                plan.version,
                plan.model_copy(update={"monitoring": Monitoring()}).model_dump_json(),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise DomainError("active_plan_exists", "该证券已有进行中计划，请先结束原计划") from error


def create_plan(engine: PlanContext, body: PlanInput, now: datetime) -> Plan:
    """计划只使用已知本地证券；不获取行情、不创建委托。"""
    item = next((item for item in engine.catalog if item.symbol == body.symbol), None)
    with engine.store.transaction() as connection:
        if item is None:
            rows = read_rows(connection, "SELECT data FROM observations WHERE symbol=?", (body.symbol,))
            if not rows:
                raise DomainError("unknown_symbol", "请先将该证券加入自选或完成启用，再记录计划", 404)
            item = ObservationInstrument.model_validate_json(rows[0][0])
        state = engine.store.load(connection)
        plan = Plan.model_validate(
            {
                **body.model_dump(),
                "id": str(uuid4()),
                "round_id": state.id,
                "currency": item.currency,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
        save_plan(connection, plan)
        return plan


def update_plan(engine: PlanContext, plan_id: str, body: PlanUpdate) -> Plan:
    """整份表单按读取版本更新；结束后的计划只允许继续读取。"""
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        previous = load_plan(connection, plan_id)
        ensure_current(state, previous)
        if previous.version != body.version:
            raise DomainError("plan_conflict", "计划已在另一设备修改，请重新载入后再保存")
        if previous.status == "ended":
            raise DomainError("plan_ended", "已结束计划只读，请创建新计划")
        if previous.symbol != body.symbol:
            raise DomainError("plan_symbol", "计划证券不能修改，请为其他证券创建计划", 422)
        plan = previous.model_copy(
            update={
                **body.model_dump(),
                "version": previous.version + 1,
                "updated_at": datetime.now(UTC),
                "monitoring": Monitoring(),
            }
        )
        save_plan(connection, plan)
        return plan


def ensure_current(state: State, plan: PlanSnapshot) -> None:
    """已归档轮次不可修改，也不可在新轮次关联下单。"""
    if state.id != plan.round_id:
        raise DomainError("archived_plan", "归档轮次的计划只读")


def order_snapshot(connection: sqlite3.Connection, state: State, request: OrderInput) -> PlanSnapshot | None:
    """在下单同一事务内核对计划及版本，返回当时依据。"""
    if request.plan_id is None:
        if request.plan_version is not None:
            raise DomainError("plan_required", "计划版本必须与计划编号一起提交", 422)
        return None
    plan = load_plan(connection, request.plan_id)
    ensure_current(state, plan)
    if plan.symbol != request.symbol:
        raise DomainError("plan_symbol", "计划与委托证券不一致", 422)
    if plan.version != request.plan_version:
        raise DomainError("plan_conflict", "计划已修改，请重新载入后再次预览")
    if plan.status == "ended":
        raise DomainError("plan_ended", "已结束计划不能关联新委托")
    return PlanSnapshot.model_validate(plan.model_dump(exclude={"monitoring"}))
