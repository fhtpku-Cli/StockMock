"""同批行情与汇率派生构成，不重拉来源。"""

from decimal import Decimal

from app.accounting import money
from app.models import Allocation, Composition, Currency, PositionView, State
from app.valuation import Valuation


def percentage(value: Decimal | None, total: Decimal | None) -> Decimal | None:
    """缺失或非正分母时比例未知。"""
    return value / total * 100 if value is not None and total is not None and total > 0 else None


def complete_sum(values: list[Decimal | None]) -> Decimal | None:
    """部分缺失不冒充完整分类金额。"""
    return None if None in values else sum((v for v in values if v is not None), Decimal(0))


def composition(state: State, positions: list[PositionView], valuation: Valuation) -> Composition:
    """现金余额包含占用，持仓分类不含现金或应收。"""

    def converted(amounts: dict[Currency, Decimal]) -> Decimal | None:
        values: list[Decimal | None] = []
        for currency, amount in amounts.items():
            rate = valuation.rates.get(currency)
            values.append(money(amount * rate) if rate is not None else (Decimal(0) if amount == 0 else None))
        return complete_sum(values)

    cash = converted(dict(state.cash))
    receivables = converted(dict(valuation.receivables))
    held = complete_sum([p.market_value_cny for p in positions])

    def grouped(*, market: bool) -> list[Allocation]:
        keys = sorted({str(p.market if market else p.currency) for p in positions})
        result: list[Allocation] = []
        for key in keys:
            value = complete_sum(
                [p.market_value_cny for p in positions if str(p.market if market else p.currency) == key]
            )
            result.append(Allocation(key=key, value_cny=value, weight_percent=percentage(value, valuation.total)))
        return result

    return Composition(
        cash_cny=cash,
        positions_cny=held,
        receivables_cny=receivables,
        cash_percent=percentage(cash, valuation.total),
        positions_percent=percentage(held, valuation.total),
        receivables_percent=percentage(receivables, valuation.total),
        by_market=grouped(market=True),
        by_currency=grouped(market=False),
        price_times=valuation.times,
        issues=valuation.issues,
    )
