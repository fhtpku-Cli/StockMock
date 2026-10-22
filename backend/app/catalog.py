"""将已核验主数据装配为交易目录，未知代码明确拒绝。"""

from typing import Final

from app.instrument_registry import catalog_rules
from app.models import DomainError, Instrument

CATALOG: Final = tuple(
    Instrument(
        symbol=rule.symbol,
        name=rule.name,
        market=rule.market,
        currency=rule.currency,
        kind=rule.kind,
        lot_size=rule.lot_size,
        tick_size=rule.tick_size,
        settlement_days=rule.settlement_days,
        tradable=rule.tradable,
        restriction=rule.restriction,
        price_limit_ratio=rule.price_limit_ratio,
        min_buy_quantity=rule.min_buy_quantity,
        quantity_step=rule.quantity_step,
        tick_rule=rule.tick_rule,
        rule_source=rule.rule_source,
        verified_on=str(rule.verified_on),
    )
    for rule in catalog_rules()
)


def instrument(symbol: str, catalog: tuple[Instrument, ...] = CATALOG) -> Instrument:
    """仅接纳包含来源与交易规则的目录条目。"""
    for item in catalog:
        if item.symbol == symbol:
            return item
    raise DomainError("unknown_symbol", "标的不在已接入清单", 404)
