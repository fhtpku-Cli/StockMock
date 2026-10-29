"""资产估值明确区分缺失行情、已确认账本与参考市值。"""

from datetime import datetime
from decimal import Decimal

from app.accounting import ACTIVE, money, reserved, sellable
from app.catalog import instrument
from app.composition import composition, percentage
from app.engine import Engine
from app.models import ActiveOrdersView, CashView, DomainError, Portfolio, PositionView, SellabilityView, WorkerView
from app.valuation import value_account


def portfolio(engine: Engine, now: datetime, worker: WorkerView) -> Portfolio:
    """汇总现金和持仓；缺失数据不按零价估值。"""
    state = engine.store.read()
    valuation = value_account(engine, state, now)
    for _attempt in range(2):
        if engine.store.read() == state:
            break
        state = engine.store.read()
        valuation = value_account(engine, state, now)
    if engine.store.read() != state:
        raise DomainError("valuation_changed", "账户正在变化，请刷新重试", 409)
    cash = [
        CashView(
            currency=c,
            balance=b,
            reserved=reserved(state, c),
            available=b - reserved(state, c),
        )
        for c, b in state.cash.items()
    ]
    warnings = ["个人模拟账本；资金交收简化；公司行动需本人核对登记", *valuation.issues]
    if any(valuation.receivables.values()):
        warnings.append("总资产包含已登记应收股息；确认到账前不可用于委托")
    positions: list[PositionView] = []
    for symbol in sorted({lot.symbol for lot in state.lots}):
        item = instrument(symbol, engine.catalog)
        quantity = sum(lot.quantity for lot in state.lots if lot.symbol == symbol)
        cost = sum((lot.cost for lot in state.lots if lot.symbol == symbol), Decimal(0))
        locked = [lot for lot in state.lots if lot.symbol == symbol and lot.sellable_at > now]
        next_unlock = min((lot.sellable_at for lot in locked), default=None)
        price = valuation.prices.get(symbol)
        value = pnl = None
        status = "行情不可用，收益待核对"
        if price is not None:
            value = money(price * quantity)
            pnl = value - cost
            status = f"参考估值；报价时间 {valuation.times[symbol].isoformat()}；公司行动需人工核对"
        positions.append(
            PositionView(
                market=item.market,
                cost=cost,
                pnl_percent=percentage(pnl, cost),
                market_value_cny=money(value * valuation.rates[item.currency])
                if value is not None and item.currency in valuation.rates
                else None,
                weight_percent=percentage(
                    money(value * valuation.rates[item.currency])
                    if value is not None and item.currency in valuation.rates
                    else None,
                    valuation.total,
                ),
                price_time=valuation.times.get(symbol),
                sellability=SellabilityView(
                    locked_quantity=sum(lot.quantity for lot in locked),
                    next_unlock_at=next_unlock,
                    next_unlock_quantity=sum(lot.quantity for lot in locked if lot.sellable_at == next_unlock),
                ),
                symbol=symbol,
                name=item.name,
                currency=item.currency,
                quantity=quantity,
                sellable=sellable(state, symbol, now),
                reserved=sum(
                    o.quantity for o in state.orders if o.symbol == symbol and o.side == "sell" and o.status in ACTIVE
                ),
                average_cost=cost / quantity,
                market_price=price,
                market_value=value,
                pnl=pnl,
                valuation_status=status,
            )
        )
    assets = valuation.total
    active_statuses = [order.status for order in state.orders if order.status in ACTIVE]
    return Portfolio(
        round_id=state.id,
        active_orders=ActiveOrdersView(
            accepted_count=active_statuses.count("accepted"),
            cancel_pending_count=active_statuses.count("cancel_pending"),
        ),
        composition=composition(state, positions, valuation),
        total_assets_cny=assets,
        pnl_cny=money(assets - Decimal(1000000)) if assets is not None else None,
        valuation_status="参考估值，持仓收益待核对" if assets is not None else "行情缺失或过期",
        cash=cash,
        positions=positions,
        worker=worker,
        warnings=warnings,
    )
