"""有效样本提示与北京时间到期提醒的幂等持久化。"""

from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.marketdata import Quote
from app.models import DomainError
from app.plan_models import Alert, Monitoring, Plan
from app.plans import ensure_current, load_plan
from app.storage import Store, read_rows


def alerts(store: Store, round_id: str) -> list[Alert]:
    """读取完整提示供分页或导出使用。"""
    with store.transaction() as connection:
        return [
            Alert.model_validate_json(row[0])
            for row in read_rows(
                connection, "SELECT data FROM plan_alerts WHERE round_id=? ORDER BY rowid DESC", (round_id,)
            )
        ]


def read_alert(store: Store, alert_id: str, now: datetime) -> Alert:
    """当前轮次确认已读，重复响应不会改变首次确认时间。"""
    with store.transaction() as connection:
        rows = read_rows(connection, "SELECT data FROM plan_alerts WHERE id=?", (alert_id,))
        if not rows:
            raise DomainError("alert_missing", "提示不存在", 404)
        alert = Alert.model_validate_json(rows[0][0])
        state = store.load(connection)
        if state.id != alert.round_id:
            raise DomainError("archived_alert", "归档提示只读")
        if alert.read_at is None:
            alert = alert.model_copy(update={"read_at": now})
            _ = connection.execute("UPDATE plan_alerts SET data=? WHERE id=?", (alert.model_dump_json(), alert.id))
        return alert


def persist_alert(store: Store, plan: Plan, alert: Alert) -> None:
    """网络观察后再确认轮次、活动状态及修订，忽略已经失效的结果。"""
    with store.transaction() as connection:
        current = load_plan(connection, plan.id)
        if current.version != plan.version or current.status != "active" or store.load(connection).id != plan.round_id:
            return
        _ = connection.execute(
            "INSERT INTO plan_alerts VALUES(?,?,?,?,?,?) ON CONFLICT(plan_id,version,condition) DO NOTHING",
            (alert.id, alert.round_id, alert.plan_id, alert.plan_version, alert.condition, alert.model_dump_json()),
        )


def due_alert(store: Store, plan: Plan, now: datetime) -> None:
    """北京时间整日到期，与报价来源是否可用无关。"""
    if plan.review_date is None or now.astimezone(ZoneInfo("Asia/Shanghai")).date() < plan.review_date:
        return
    persist_alert(
        store,
        plan,
        Alert(
            id=str(uuid4()),
            round_id=plan.round_id,
            plan_id=plan.id,
            plan_version=plan.version,
            symbol=plan.symbol,
            condition="review_due",
            created_at=now,
            message=f"已到计划复盘日期 {plan.review_date.isoformat()}（北京时间）",
        ),
    )


def observe(plan: Plan, quote: Quote | None, now: datetime) -> Monitoring:
    """完整成交样本用于保守判断；未来、超二十分钟或零量均不可触价。"""
    if quote is None:
        return Monitoring(status="unavailable", message="行情缺失或来源失败，暂不能判断到价", checked_at=now)
    valid = (
        quote.price.is_finite()
        and quote.market_time.utcoffset() is not None
        and quote.retrieved_at.utcoffset() is not None
        and quote.interval_start is not None
        and quote.interval_start.utcoffset() is not None
        and bool(quote.source.strip())
        and quote.tradable
        and quote.symbol == plan.symbol
        and quote.currency == plan.currency
        and quote.price > 0
        and quote.volume is not None
        and quote.volume.is_finite()
        and quote.volume > 0
        and quote.interval_start < quote.market_time
        and quote.market_time - quote.interval_start <= timedelta(minutes=5)
        and quote.market_time <= quote.retrieved_at <= now
        and quote.market_time <= now
        and now - quote.market_time <= timedelta(minutes=20)
    )
    return Monitoring(
        status="valid" if valid else "unavailable",
        checked_at=now,
        market_time=quote.market_time,
        price=quote.price if quote.price.is_finite() else None,
        source=quote.source,
        message="已观察到有效延迟样本，不代表精准触价" if valid else "报价未来、过期、停牌或未核验，暂不能判断到价",
    )


def price_alerts(store: Store, plan: Plan, observation: Monitoring) -> None:
    """保存每修订观察状态；有效样本只产生一次目标和止损提示。"""
    with store.transaction() as connection:
        current = load_plan(connection, plan.id)
        state = store.load(connection)
        if current.version != plan.version or current.status != "active" or state.id != plan.round_id:
            return
        ensure_current(state, current)
        _ = connection.execute(
            """INSERT INTO plan_monitoring VALUES(?,?,?) ON CONFLICT(plan_id) DO UPDATE SET
            version=excluded.version,data=excluded.data""",
            (plan.id, plan.version, observation.model_dump_json()),
        )
    if observation.status != "valid" or observation.price is None or observation.checked_at is None:
        return
    conditions: list[Literal["target", "stop"]] = []
    if plan.target_price is not None and observation.price >= plan.target_price:
        conditions.append("target")
    if plan.stop_price is not None and observation.price <= plan.stop_price:
        conditions.append("stop")
    for condition in conditions:
        label = "目标参考价" if condition == "target" else "止损参考价"
        threshold = plan.target_price if condition == "target" else plan.stop_price
        message = f"观察到有效样本达到当时{label} {threshold} {plan.currency}；"
        message += "仅供复盘，不自动交易，不代表精准触价"
        persist_alert(
            store,
            plan,
            Alert(
                id=str(uuid4()),
                round_id=plan.round_id,
                plan_id=plan.id,
                plan_version=plan.version,
                symbol=plan.symbol,
                condition=condition,
                created_at=observation.checked_at,
                market_time=observation.market_time,
                price=observation.price,
                source=observation.source,
                message=message,
            ),
        )
