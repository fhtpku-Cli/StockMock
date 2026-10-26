"""换汇和练习轮次的幂等事务。"""

from datetime import datetime, timedelta
from uuid import uuid4

from app.accounting import ACTIVE, EntryContext, available, entry, money
from app.api_models import ConvertInput, ResetInput, RoundResult
from app.engine import Engine
from app.models import DomainError, State
from app.storage import read_rows


def convert(engine: Engine, request: ConvertInput, now: datetime) -> None:
    """按真实参考汇率原子兑换可用外币。"""
    payload = "convert:" + request.model_dump_json()
    with engine.store.transaction() as connection:
        rows = read_rows(connection, "SELECT payload FROM requests WHERE id=?", (request.request_id,))
        if rows:
            if rows[0][0] != payload:
                raise DomainError("idempotency_conflict", "请求编号已用于其他操作")
            return
        round_id = engine.store.load(connection).id
    rates = engine.provider.fx_rates()
    rate = next((r for r in rates if r.currency == request.currency), None)
    if rate is None or now - rate.market_time > timedelta(days=5) or rate.market_time > now:
        raise DomainError("fx_unavailable", "参考汇率不可用或过期")
    with engine.store.transaction() as connection:
        rows = read_rows(connection, "SELECT payload FROM requests WHERE id=?", (request.request_id,))
        if rows:
            row = rows[0]
            if row[0] != payload:
                raise DomainError("idempotency_conflict", "请求编号已用于其他操作")
            return
        state = engine.store.load(connection)
        if state.id != round_id:
            raise DomainError("round_changed", "练习轮次已变化，请刷新后重新操作")
        if available(state, request.currency) < request.amount:
            raise DomainError("insufficient_funds", "可用外币余额不足")
        cny = money(request.amount * rate.cny_per_unit)
        state.cash[request.currency] -= request.amount
        state.cash["CNY"] += cny
        note = f"{rate.source} {rate.market_time.isoformat()} 汇率 {rate.cny_per_unit}"
        entry(state, "fx_out", request.currency, -request.amount, EntryContext(now, None, note))
        entry(state, "fx_in", "CNY", cny, EntryContext(now, None, note))
        engine.store.save(connection, state)
        _ = connection.execute("INSERT INTO requests VALUES(?,?,?)", (request.request_id, payload, "{}"))


def reset(engine: Engine, request: ResetInput, now: datetime) -> RoundResult:
    """归档旧轮次并在无活动订单时重开练习。"""
    payload = "reset:" + request.model_dump_json()
    with engine.store.transaction() as connection:
        rows = read_rows(connection, "SELECT payload,response FROM requests WHERE id=?", (request.request_id,))
        if rows:
            row = rows[0]
            if row[0] != payload:
                raise DomainError("idempotency_conflict", "请求编号已用于其他操作")
            return RoundResult.model_validate_json(row[1])
        state = engine.store.load(connection)
        if any(o.status in ACTIVE for o in state.orders):
            raise DomainError("open_orders", "请等待全部挂单结束后再重开练习")
        engine.store.save(connection, state.model_copy(update={"archived_at": now}))
        fresh = State(id=str(uuid4()), created_at=now)
        engine.store.save(connection, fresh)
        result = RoundResult(round_id=fresh.id)
        _ = connection.execute(
            "INSERT INTO requests VALUES(?,?,?)",
            (request.request_id, payload, result.model_dump_json()),
        )
        return result
