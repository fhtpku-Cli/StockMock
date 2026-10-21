"""资产估值明确区分缺失行情、已确认账本与参考市值。"""

from datetime import datetime
from decimal import Decimal

from app.accounting import ACTIVE, money, reserved, sellable
from app.catalog import instrument
from app.engine import Engine
from app.marketdata import MarketDataError
from app.models import CashView, Portfolio, PositionView, WorkerView


def portfolio(engine: Engine, now: datetime, worker: WorkerView) -> Portfolio:
    """汇总现金和持仓；缺失数据不按零价估值。"""
    state = engine.store.read()
    cash = [
        CashView(
            currency=c,
            balance=b,
            reserved=reserved(state, c),
            available=b - reserved(state, c),
        )
        for c, b in state.cash.items()
    ]
    rates = {"CNY": Decimal(1)}
    warnings = ["个人模拟账本；资金交收简化；分红、拆股尚未自动处理，持仓收益需人工核对"]
    if any(b for c, b in state.cash.items() if c != "CNY") or any(
        instrument(lot.symbol, engine.catalog).currency != "CNY" for lot in state.lots
    ):
        try:
            rates.update({r.currency: r.cny_per_unit for r in engine.provider.fx_rates()})
        except MarketDataError:
            warnings.append("外汇估值数据不可用，总资产暂不计算")
    total = sum((b * rates.get(c, Decimal(0)) for c, b in state.cash.items()), Decimal(0))
    complete = all(c in rates or not b for c, b in state.cash.items())
    positions: list[PositionView] = []
    for symbol in sorted({lot.symbol for lot in state.lots}):
        item = instrument(symbol, engine.catalog)
        quantity = sum(lot.quantity for lot in state.lots if lot.symbol == symbol)
        cost = sum((lot.cost for lot in state.lots if lot.symbol == symbol), Decimal(0))
        price = value = pnl = None
        status = "行情不可用，收益待核对"
        try:
            quote = engine.provider.quote(symbol)
            price = quote.price
            value = money(price * quantity)
            pnl = value - cost
            status = f"参考估值；报价时间 {quote.market_time.isoformat()}；公司行动待核对"
            if item.currency in rates:
                total += value * rates[item.currency]
            else:
                complete = False
        except MarketDataError:
            complete = False
        positions.append(
            PositionView(
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
    assets = money(total) if complete else None
    return Portfolio(
        round_id=state.id,
        total_assets_cny=assets,
        pnl_cny=money(total - Decimal(1000000)) if complete else None,
        valuation_status="参考估值，持仓收益待核对"
        if state.lots and complete
        else ("完整" if complete else "行情缺失"),
        cash=cash,
        positions=positions,
        worker=worker,
        warnings=warnings,
    )
