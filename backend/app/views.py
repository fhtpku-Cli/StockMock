"""资产估值明确区分缺失行情、已确认账本与参考市值。"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Final, Literal, final

from app.accounting import ACTIVE, money, reserved, sellable
from app.activated_registry import RuleProvider
from app.calendar import calendar
from app.catalog import instrument
from app.composition import composition, percentage
from app.engine import Engine
from app.marketdata_models import MarketDataError
from app.marketdata_reference import QuoteReference
from app.models import (
    ActiveOrdersView,
    CashView,
    ChangeStatus,
    DomainError,
    Portfolio,
    PositionView,
    SellabilityView,
    State,
    WorkerView,
)
from app.valuation import Valuation, value_account

Market = Literal["CN", "HK", "US"]

# 当日日线在收盘后多久视为定稿；None 是实测前的过渡值，当日日线一律不算完整。
CLOSE_BUFFER: Final[dict[Market, timedelta | None]] = {"CN": None, "HK": None, "US": None}
# 2026-11-03 三市场已按计划第 14 节通过分时段核对并开放涨跌，依据见 outputs/行情接入实测.md。
# 保留按市场关闭涨跌的机制；港股开放仅涉及展示，不改变交易权限。
CHANGE_DISABLED_MARKETS: Final[frozenset[Market]] = frozenset()
_CHANGE_ERRORS: Final = (MarketDataError, ValueError, IndexError, DomainError)


@dataclass(frozen=True, slots=True)
class Change:
    """只供展示的相对供应商昨收涨跌；不进入估值、快照或账本。"""

    previous_close: Decimal | None = None
    change: Decimal | None = None
    change_percent: Decimal | None = None
    change_day: date | None = None
    change_base_day: date | None = None
    change_status: ChangeStatus | None = None


def bar_complete(market: Market, day: date, retrieved_at: datetime, buffer: timedelta | None) -> bool | None:
    """按日线取得时刻判定是否已定稿，与当前时间无关；日历不可用时未知。"""
    try:
        cal = calendar(market)
        retrieved_day = retrieved_at.astimezone(cal.tz).date()
        if day < retrieved_day:
            return True
        if day > retrieved_day:
            return False
        session = cal.date_to_session(str(day), direction="previous")
        if session.to_pydatetime().date() != day:
            return False
        close = cal.session_close(session).to_pydatetime()
    except ValueError:
        return None
    return buffer is not None and retrieved_at >= close + buffer


def quote_change(market: Market, reference: QuoteReference) -> Change:
    """现价与昨收出自同一响应；衍生计算失败只隐藏涨跌，不影响价格展示。"""
    if market in CHANGE_DISABLED_MARKETS:
        return Change(change_status="market_disabled")
    try:
        cal = calendar(market)
        quote_time = reference.quote.market_time
        day = quote_time.astimezone(cal.tz).date()
        session = cal.date_to_session(str(day), direction="previous")
        if session.to_pydatetime().date() != day:
            return Change(change_status="unknown")
        if quote_time < cal.session_open(session).to_pydatetime():
            return Change(change_day=day, change_status="pre_open")
        base = cal.previous_session(session).to_pydatetime().date()
    except _CHANGE_ERRORS:
        return Change(change_status="unknown")
    previous = reference.previous_close
    if previous is None:
        return Change(change_day=day, change_base_day=base, change_status="no_previous_close")
    change = reference.quote.price - previous
    return Change(previous, change, percentage(change, previous), day, base, "available")


def position_change(
    market: Market, symbol: str, price: Decimal | None, price_time: datetime | None, display: RuleProvider | None
) -> Change:
    """只对已有估值价格的持仓请求昨收；两次报价不一致时不输出涨跌。"""
    if display is None:
        return Change()
    if price is None or price_time is None:
        return Change(change_status="no_price")
    try:
        reference = display.quote_reference(symbol)
    except _CHANGE_ERRORS:
        return Change(change_status="unknown")
    if reference.quote.market_time != price_time or reference.quote.price != price:
        return Change(change_status="quote_changed")
    return quote_change(market, reference)


@dataclass(frozen=True, slots=True)
class ValuedState:
    """同一轮资产计算的分母、报价、汇率和账户状态。"""

    state: State
    valuation: Valuation
    at: datetime


@final
class ValuationCache:
    """每个应用保留最近一次通过一致性检查的估值。"""

    def __init__(self) -> None:
        """建立只供本应用使用的空缓存。"""
        self.lock = Lock()
        self.latest: ValuedState | None = None

    def put(self, record: ValuedState) -> None:
        """一次替换完整记录，避免读到混合状态。"""
        with self.lock:
            self.latest = record

    def match(self, state: State, now: datetime) -> ValuedState | None:
        """仅返回六十秒内与本次预览账本相同的估值。"""
        with self.lock:
            lookup = self.latest
        if lookup and timedelta(0) <= now - lookup.at <= timedelta(seconds=60) and lookup.state == state:
            return lookup
        return None


def value_cny(value: Decimal | None, rate: Decimal | None) -> Decimal | None:
    """用已舍入的原币市值计算人民币市值；缺价或缺汇率保留未知。"""
    return money(value * rate) if value is not None and rate is not None else None


def holding_weight(value: Decimal | None, rate: Decimal | None, total: Decimal | None) -> Decimal | None:
    """资产页和预览共享原币市值、折算和分母。"""
    return percentage(value_cny(value, rate), total)


def portfolio(
    engine: Engine,
    now: datetime,
    worker: WorkerView,
    display: RuleProvider | None = None,
    *,
    cache: ValuationCache | None = None,
) -> Portfolio:
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
    if cache is not None:
        cache.put(ValuedState(state, valuation, now))
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
        change = position_change(item.market, symbol, price, valuation.times.get(symbol), display)
        positions.append(
            PositionView(
                market=item.market,
                cost=cost,
                pnl_percent=percentage(pnl, cost),
                market_value_cny=value_cny(value, valuation.rates.get(item.currency)),
                weight_percent=holding_weight(value, valuation.rates.get(item.currency), valuation.total),
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
                previous_close=change.previous_close,
                change=change.change,
                change_percent=change.change_percent,
                change_day=change.change_day,
                change_base_day=change.change_base_day,
                change_status=change.change_status,
            )
        )
    assets = valuation.total
    active_statuses = [order.status for order in state.orders if order.status in ACTIVE]
    valuation_status = "行情缺失或过期"
    if assets is not None:
        valuation_status = "当前无持仓" if not positions else "参考估值，持仓收益待核对"
    return Portfolio(
        round_id=state.id,
        active_orders=ActiveOrdersView(
            accepted_count=active_statuses.count("accepted"),
            cancel_pending_count=active_statuses.count("cancel_pending"),
        ),
        composition=composition(state, positions, valuation),
        total_assets_cny=assets,
        pnl_cny=money(assets - Decimal(1000000)) if assets is not None else None,
        valuation_status=valuation_status,
        cash=cash,
        positions=positions,
        worker=worker,
        warnings=warnings,
    )
