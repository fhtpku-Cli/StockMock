"""在同一事务内记录人工公司行动与现金、份额变化。"""

from datetime import datetime
from decimal import Decimal
from typing import assert_never
from uuid import uuid4

from app.accounting import ACTIVE, EntryContext, entry, money
from app.calendar import calendar
from app.catalog import instrument
from app.engine import Engine
from app.models import DomainError, Lot, State
from app.personal_models import Adjustment, AdjustmentInput, PaymentInput
from app.storage import Store, read_rows


def records(store: Store, round_id: str) -> list[Adjustment]:
    """读取指定轮次的人工调整审计，不修改归档。"""
    with store.transaction() as connection:
        return [
            Adjustment.model_validate_json(row[0])
            for row in read_rows(
                connection, "SELECT data FROM adjustments WHERE round_id=? ORDER BY rowid", (round_id,)
            )
        ]


def validate_action(engine: Engine, state: State, request: AdjustmentInput, now: datetime) -> list[Lot]:
    """阻止跨轮次、历史追改与未解决委托造成份额歧义。"""
    item = instrument(request.symbol, engine.catalog)
    zone = calendar(item.market).tz
    if request.round_id != state.id:
        raise DomainError("round_changed", "练习轮次已变化，请重新核对")
    if request.effective_date > now.astimezone(zone).date():
        raise DomainError("future_action", "不能登记尚未发生的公司行动")
    if any(order.symbol == item.symbol and order.status in ACTIVE for order in state.orders):
        raise DomainError("open_orders", "请先结束该标的全部挂单和待确认撤单")
    lots = [lot for lot in state.lots if lot.symbol == item.symbol]
    if sum(lot.quantity for lot in lots) != request.expected_quantity:
        raise DomainError("position_changed", "当前持仓份额与确认值不一致，请重新核对")
    if any(
        order.symbol == item.symbol
        and order.filled_at is not None
        and order.filled_at.astimezone(zone).date() >= request.effective_date
        for order in state.orders
    ):
        raise DomainError("later_trades", "生效日当天或之后已有成交，无法确认日期级权益份额")
    return lots


def apply_action(engine: Engine, request: AdjustmentInput, now: datetime) -> Adjustment:
    """幂等登记股息或整数拆并股；历史成交价格永不重写。"""
    payload = "adjustment:" + request.model_dump_json()
    with engine.store.transaction() as connection:
        prior = read_rows(connection, "SELECT payload,response FROM requests WHERE id=?", (request.request_id,))
        if prior:
            if prior[0][0] != payload:
                raise DomainError("idempotency_conflict", "请求编号已用于其他操作")
            return Adjustment.model_validate_json(prior[0][1])
        state = engine.store.load(connection)
        lots = validate_action(engine, state, request, now)
        existing = [
            Adjustment.model_validate_json(row[0])
            for row in read_rows(
                connection, "SELECT data FROM adjustments WHERE round_id=? AND symbol=?", (state.id, request.symbol)
            )
        ]
        if any(action.effective_date > request.effective_date for action in existing):
            raise DomainError("later_adjustment", "已有更晚公司行动，不允许追改早期记录")
        if any(action.effective_date == request.effective_date and action.kind == request.kind for action in existing):
            raise DomainError("duplicate_action", "同一证券、日期与类型已登记，不能重复计入")
        item = instrument(request.symbol, engine.catalog)
        cost = sum((lot.cost for lot in lots), Decimal(0))
        quantity, cash, receivable = changes(state, request, lots, now)
        if request.kind == "split" and item.market == "HK" and quantity % item.lot_size:
            raise DomainError("odd_lot_unsupported", "拆并股后产生港股非整手持仓，当前不支持零股报价，未执行调整")
        paid_at = now if cash else None
        action = Adjustment(
            request_id=request.request_id,
            round_id=request.round_id,
            symbol=request.symbol,
            kind=request.kind,
            effective_date=request.effective_date,
            expected_quantity=request.expected_quantity,
            note=request.note,
            cash_per_share=request.cash_per_share,
            payment_status=request.payment_status,
            numerator=request.numerator,
            denominator=request.denominator,
            id=str(uuid4()),
            created_at=now,
            quantity_before=request.expected_quantity,
            quantity_after=quantity,
            cash_delta=cash,
            receivable_amount=receivable,
            currency=item.currency,
            cost_before=cost,
            cost_after=cost,
            paid_at=paid_at,
        )
        state.cash[item.currency] += cash
        note = f"调整 {action.id} {item.symbol} 生效 {request.effective_date}；"
        note += f"份额 {request.expected_quantity}→{quantity}；应收 {receivable}；{request.note}"
        entry(
            state,
            "dividend_receivable" if receivable else request.kind,
            item.currency,
            cash,
            EntryContext(
                now,
                None,
                note,
            ),
        )
        engine.store.save(connection, state)
        _ = connection.execute(
            "INSERT INTO adjustments VALUES(?,?,?,?,?,?)",
            (
                action.id,
                state.id,
                item.symbol,
                request.kind,
                str(request.effective_date),
                action.model_dump_json(),
            ),
        )
        _ = connection.execute(
            "INSERT INTO requests VALUES(?,?,?)", (request.request_id, payload, action.model_dump_json())
        )
        return action


def changes(state: State, request: AdjustmentInput, lots: list[Lot], now: datetime) -> tuple[int, Decimal, Decimal]:
    """计算税前股息或整股拆并股，成本和解锁条件不凭空改变。"""
    quantity = request.expected_quantity
    match request.kind:
        case "dividend":
            if request.cash_per_share is None or request.numerator is not None or request.denominator is not None:
                raise DomainError("invalid_dividend", "请填写税前每股股息，不填写拆股比例", 422)
            amount = money(request.cash_per_share * quantity)
            if amount <= 0:
                raise DomainError("invalid_dividend", "股息金额不足最小货币单位", 422)
            match request.payment_status:
                case "paid":
                    return quantity, amount, Decimal(0)
                case "receivable":
                    return quantity, Decimal(0), amount
                case _:
                    assert_never(request.payment_status)
        case "split":
            return split_shares(state, request, lots, now), Decimal(0), Decimal(0)
        case _:
            assert_never(request.kind)


def split_shares(state: State, request: AdjustmentInput, lots: list[Lot], now: datetime) -> int:
    """只执行整股比例变更并保留完整总成本。"""
    if request.numerator is None or request.denominator is None or request.cash_per_share is not None:
        raise DomainError("invalid_split", "请填写完整拆并股比例，不填写股息", 422)
    if request.payment_status != "paid" or request.numerator == request.denominator:
        raise DomainError("invalid_split", "拆并股须有实际比例变化且不使用应收状态", 422)
    if request.expected_quantity * request.numerator % request.denominator:
        raise DomainError("fractional_unsupported", "结果含碎股，暂不支持现金替代或截断份额")
    if any(lot.sellable_at > now for lot in lots):
        raise DomainError("unsettled_position", "请等待全部持仓可卖后再核对拆并股")
    quantity = request.expected_quantity * request.numerator // request.denominator
    cost = sum((lot.cost for lot in lots), Decimal(0))
    state.lots[:] = [lot for lot in state.lots if lot.symbol != request.symbol]
    state.lots.append(Lot(symbol=request.symbol, quantity=quantity, cost=cost, sellable_at=now))
    return quantity


def receive_dividend(engine: Engine, action_id: str, request: PaymentInput, now: datetime) -> Adjustment:
    """本人确认到账后，将应收转为现金；重试不重复入账。"""
    payload = "payment:" + action_id + ":" + request.model_dump_json()
    with engine.store.transaction() as connection:
        prior = read_rows(connection, "SELECT payload,response FROM requests WHERE id=?", (request.request_id,))
        if prior:
            if prior[0][0] != payload:
                raise DomainError("idempotency_conflict", "请求编号已用于其他操作")
            return Adjustment.model_validate_json(prior[0][1])
        state = engine.store.load(connection)
        if state.id != request.round_id:
            raise DomainError("round_changed", "不能修改归档轮次")
        rows = read_rows(connection, "SELECT data FROM adjustments WHERE id=? AND round_id=?", (action_id, state.id))
        if not rows:
            raise DomainError("adjustment_missing", "找不到当前轮次的股息记录", 404)
        action = Adjustment.model_validate_json(rows[0][0])
        if action.kind != "dividend" or action.paid_at is not None or action.receivable_amount <= 0:
            raise DomainError("not_receivable", "此记录没有待到账股息")
        amount = action.receivable_amount
        state.cash[action.currency] += amount
        entry(state, "dividend_payment", action.currency, amount, EntryContext(now, None, f"股息到账 {action.id}"))
        result = action.model_copy(
            update={"paid_at": now, "payment_status": "paid", "cash_delta": amount, "receivable_amount": Decimal(0)}
        )
        _ = connection.execute("UPDATE adjustments SET data=? WHERE id=?", (result.model_dump_json(), action_id))
        engine.store.save(connection, state)
        _ = connection.execute(
            "INSERT INTO requests VALUES(?,?,?)", (request.request_id, payload, result.model_dump_json())
        )
        return result
