"""小范围观察清单；未核验的真实行情不进入撮合。"""

from decimal import Decimal
from typing import Final

from app.models import DomainError, Instrument

CATALOG: Final = (
    Instrument(
        symbol="600519.SS",
        name="贵州茅台",
        market="CN",
        currency="CNY",
        kind="stock",
        lot_size=100,
        tick_size=Decimal(".01"),
        settlement_days=1,
        tradable=True,
        restriction="仅常规时段完整五分钟样本；停牌和公司行动需人工核对",
    ),
    Instrument(
        symbol="510300.SS",
        name="沪深300ETF",
        market="CN",
        currency="CNY",
        kind="ETF",
        lot_size=100,
        tick_size=Decimal(".001"),
        settlement_days=1,
        tradable=True,
        restriction="仅常规时段完整五分钟样本；停牌和公司行动需人工核对",
    ),
    Instrument(
        symbol="000001.SZ",
        name="平安银行",
        market="CN",
        currency="CNY",
        kind="stock",
        lot_size=100,
        tick_size=Decimal(".01"),
        settlement_days=1,
        tradable=True,
        restriction="仅常规时段完整五分钟样本；停牌和公司行动需人工核对",
    ),
    Instrument(
        symbol="159915.SZ",
        name="创业板ETF",
        market="CN",
        currency="CNY",
        kind="ETF",
        lot_size=100,
        tick_size=Decimal(".001"),
        settlement_days=1,
        tradable=True,
        restriction="仅常规时段完整五分钟样本；停牌和公司行动需人工核对",
    ),
    Instrument(
        symbol="0700.HK",
        name="腾讯控股",
        market="HK",
        currency="HKD",
        kind="stock",
        lot_size=100,
        tick_size=Decimal(".2"),
    ),
    Instrument(
        symbol="2800.HK",
        name="盈富基金",
        market="HK",
        currency="HKD",
        kind="ETF",
        lot_size=500,
        tick_size=Decimal(".02"),
    ),
    Instrument(
        symbol="AAPL",
        name="苹果",
        market="US",
        currency="USD",
        kind="stock",
        lot_size=1,
        tick_size=Decimal(".01"),
    ),
    Instrument(
        symbol="SPY",
        name="标普500ETF",
        market="US",
        currency="USD",
        kind="ETF",
        lot_size=1,
        tick_size=Decimal(".01"),
    ),
)


def instrument(symbol: str, catalog: tuple[Instrument, ...] = CATALOG) -> Instrument:
    """解析已支持标的，拒绝任意未知代码。"""
    for item in catalog:
        if item.symbol == symbol:
            return item
    raise DomainError("unknown_symbol", "标的不在已接入清单", 404)
