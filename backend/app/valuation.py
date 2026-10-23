"""统一当前资产和每日快照的缺失、过期估值判断。"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from app.accounting import money
from app.calendar import calendar, is_open
from app.catalog import instrument
from app.engine import Engine
from app.marketdata import MarketDataError
from app.models import Currency, Instrument, State
from app.personal_models import Adjustment
from app.storage import read_rows


@dataclass(frozen=True, slots=True)
class Valuation:
    """缺失或过期时总额为空，避免把旧价格绘成收益。"""

    total: Decimal | None
    status: Literal["complete", "stale", "missing"]
    issues: list[str]
    prices: dict[str, Decimal]
    times: dict[str, datetime]
    receivables: dict[Currency, Decimal]
    rates: dict[Currency, Decimal]


def stale_price(item: Instrument, event: datetime, now: datetime) -> bool:
    """开市按延迟窗口，休市按最近已发生交易日判定时效。"""
    if event > now:
        return True
    if is_open(item, now):
        return now - event > timedelta(minutes=20)
    cal = calendar(item.market)
    session = cal.date_to_session(str(now.astimezone(cal.tz).date()), direction="previous")
    if now < cal.session_open(session).to_pydatetime():
        session = cal.previous_session(session)
    close = cal.session_close(session).to_pydatetime() - (timedelta(minutes=3) if item.market == "CN" else timedelta(0))
    reference = close
    if now < close:
        lunch = {"CN": time(11, 30), "HK": time(12)}.get(item.market)
        if lunch is not None:
            reference = datetime.combine(now.astimezone(cal.tz).date(), lunch, cal.tz)
    return event < reference - timedelta(minutes=20)


def value_account(engine: Engine, state: State, now: datetime) -> Valuation:
    """读取账户需要的价格与汇率，保留各来源实际市场时间。"""
    rates: dict[Currency, Decimal] = {"CNY": Decimal(1)}
    receivables: dict[Currency, Decimal] = {"CNY": Decimal(0), "HKD": Decimal(0), "USD": Decimal(0)}
    actions = valuation_actions(engine, state)
    if actions is None:
        return Valuation(None, "missing", ["账户在估值期间变化，请刷新后查看"], {}, {}, receivables, rates)
    for action in actions:
        receivables[action.currency] += action.receivable_amount
    needed: set[Currency] = {currency for currency, cash in state.cash.items() if cash or receivables[currency]}
    needed.update(instrument(lot.symbol, engine.catalog).currency for lot in state.lots)
    issues: list[str] = []
    times: dict[str, datetime] = {}
    prices: dict[str, Decimal] = {}
    missing = stale = False
    try:
        rates, fx_times, fx_issues = valuation_rates(engine, needed, now)
        times.update(fx_times)
        issues.extend(fx_issues)
    except MarketDataError:
        issues.append("外汇来源不可用")
    if needed - rates.keys():
        missing = True
        issues.append("缺少账户所需有效汇率")
    total = sum(
        (
            money(cash * rates.get(currency, Decimal(0)))
            + money(receivables[currency] * rates.get(currency, Decimal(0)))
            for currency, cash in state.cash.items()
        ),
        Decimal(0),
    )
    for symbol in sorted({lot.symbol for lot in state.lots}):
        item = instrument(symbol, engine.catalog)
        try:
            quote = engine.provider.quote(symbol)
            times[symbol] = quote.market_time
            if quote.symbol != symbol or quote.currency != item.currency or quote.price <= 0:
                missing = True
                issues.append(f"{symbol} 报价内容不适用")
                continue
            last_split = max((a.created_at for a in actions if a.symbol == symbol and a.kind == "split"), default=None)
            if stale_price(item, quote.market_time, now) or (
                last_split is not None and quote.market_time <= last_split
            ):
                stale = True
                issues.append(f"{symbol} 报价过期或尚未反映拆并股")
                continue
            prices[symbol] = quote.price
            total += money(
                money(quote.price * sum(lot.quantity for lot in state.lots if lot.symbol == symbol))
                * rates.get(item.currency, Decimal(0))
            )
        except (MarketDataError, ValueError, IndexError):
            missing = True
            issues.append(f"{symbol} 行情或交易日历不可用")
    if engine.store.read() != state:
        return Valuation(None, "missing", ["账户在估值期间变化，请刷新后查看"], {}, {}, {}, {})
    status = valuation_status(missing=missing, stale=stale)
    return Valuation(money(total) if status == "complete" else None, status, issues, prices, times, receivables, rates)


def valuation_rates(
    engine: Engine,
    needed: set[Currency],
    now: datetime,
) -> tuple[dict[Currency, Decimal], dict[str, datetime], list[str]]:
    """仅接纳账户所需且未超过五日的参考汇率。"""
    rates: dict[Currency, Decimal] = {"CNY": Decimal(1)}
    times: dict[str, datetime] = {}
    issues: list[str] = []
    if not needed - {"CNY"}:
        return rates, times, issues
    for rate in engine.provider.fx_rates():
        if rate.currency in needed:
            times[f"FX:{rate.currency}"] = rate.market_time
            if rate.cny_per_unit <= 0 or rate.market_time > now or now - rate.market_time > timedelta(days=5):
                issues.append(f"{rate.currency} 汇率过期或时间异常")
            else:
                rates[rate.currency] = rate.cny_per_unit
    return rates, times, issues


def valuation_status(*, missing: bool, stale: bool) -> Literal["complete", "stale", "missing"]:
    """缺失优先，过期仍不可计算完整总额。"""
    return "missing" if missing else ("stale" if stale else "complete")


def valuation_actions(engine: Engine, state: State) -> list[Adjustment] | None:
    """在同一短事务读取状态与应收，网络请求不占写事务。"""
    with engine.store.transaction() as connection:
        if engine.store.load(connection) != state:
            return None
        return [
            Adjustment.model_validate_json(row[0])
            for row in read_rows(connection, "SELECT data FROM adjustments WHERE round_id=?", (state.id,))
        ]
