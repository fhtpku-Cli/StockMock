"""委托受理与事件时间撮合，行情获取在事务外执行。"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol, final

from app.accounting import ACTIVE, FEE, money
from app.action_guard import check_actions
from app.activated_registry import ActivatedRegistry
from app.calendar import is_open, prior_session_day, window
from app.catalog import CATALOG, from_rule, instrument
from app.marketdata import Bar, FxRate, MarketDataError, Quote
from app.matching import advance
from app.models import DomainError, Instrument, Order, OrderInput, Preview
from app.order_builder import OrderContext, build_order
from app.order_requests import retry_order
from app.plans import order_snapshot
from app.storage import Store


class Provider(Protocol):
    """行情边界；测试可替换实现而不能公开注入报价。"""

    def quote(self, symbol: str) -> Quote:
        """返回估值快照。"""
        ...

    def fx_rates(self) -> tuple[FxRate, ...]:
        """返回参考汇率。"""
        ...

    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        """返回完整成交样本。"""
        ...

    def history(self, symbol: str) -> tuple[Bar, ...]:
        """返回未复权日线。"""
        ...


@final
class Engine:
    """在持久事务内协调委托与市场事件。"""

    def __init__(
        self,
        store: Store,
        provider: Provider,
        catalog: tuple[Instrument, ...] = CATALOG,
    ) -> None:
        """保存依赖并初始化本实例的状态。"""
        self.store = store
        self.provider = provider
        self.plan_provider: Provider = provider
        self.registry = ActivatedRegistry(store, catalog)
        self._catalog = catalog
        self.paused: frozenset[str] = frozenset()

    @property
    def catalog(self) -> tuple[Instrument, ...]:
        """基础规则优先，持久扩展补齐，人工暂停始终最后覆盖。"""
        merged = {item.symbol: item for item in self._catalog}
        for rule in self.registry.rules:
            _ = merged.setdefault(rule.symbol, from_rule(rule))
        return tuple(
            item.model_copy(update={"tradable": False, "restriction": "公司行动或规则待人工核对，已暂停交易"})
            if item.symbol in self.paused
            else item
            for item in merged.values()
        )

    @catalog.setter
    def catalog(self, value: tuple[Instrument, ...]) -> None:
        self._catalog = value
        self.registry.catalog = value

    def prepare(self, request: OrderInput, now: datetime) -> tuple[Quote, FxRate | None, Decimal | None]:
        """在写事务外获取委托所需的行情和参考汇率。"""
        item = instrument(request.symbol, self.catalog)
        if not item.tradable:
            raise DomainError("observation_only", item.restriction)
        if request.order_type == "market" and not is_open(item, now):
            raise DomainError("market_closed", "市价模拟只在常规连续交易时段受理")
        samples = self.provider.intraday(item.symbol)
        if not samples:
            raise DomainError("quote_unavailable", "暂无完整的可成交分钟样本")
        quote = samples[-1]
        if (
            not quote.tradable
            or quote.symbol != item.symbol
            or quote.currency != item.currency
            or quote.price <= 0
            or quote.market_time > now
            or quote.interval_start is None
            or quote.volume is None
            or quote.volume <= 0
        ):
            raise DomainError("quote_unverified", "当前行情不能用于成交，请等待已核验样本")
        if is_open(item, now) and now - quote.market_time > timedelta(minutes=20):
            raise DomainError("quote_stale", "行情超过容忍时效，暂停新订单")
        with self.store.transaction() as connection:
            state = self.store.load(connection)
            check_actions(connection, state.id, item, quote, now)
        rate = self.optional_rate(item, request, now)
        reference = None
        if item.market == "CN":
            opening, _ = window(item, now)
            reference_day = prior_session_day(item, opening)
            previous = [b for b in self.provider.history(item.symbol) if b.day == reference_day]
            if not previous:
                raise DomainError("reference_unavailable", "缺少前一交易日未复权收盘价")
            reference = max(previous, key=lambda b: b.day).close
        return quote, rate, reference

    def optional_rate(self, item: Instrument, request: OrderInput, now: datetime) -> FxRate | None:
        """外币可独立交易；仅发生兑换时才强制要求有效汇率。"""
        rate = None
        if item.currency != "CNY" and request.side == "buy":
            try:
                rate = next(
                    (r for r in self.provider.fx_rates() if r.currency == item.currency),
                    None,
                )
                if rate is not None and (now - rate.market_time > timedelta(days=5) or rate.market_time > now):
                    rate = None
            except MarketDataError:
                rate = None
        return rate

    def preview(self, request: OrderInput, now: datetime) -> Preview:
        """检查委托资源并返回预览，不改变账户。"""
        with self.store.transaction() as connection:
            _ = order_snapshot(connection, self.store.load(connection), request)
        quote, rate, reference = self.prepare(request, now)
        order = build_order(
            instrument(request.symbol, self.catalog),
            self.store.read(),
            request,
            OrderContext(quote, rate, now, reference),
        )
        conversion_rate = rate if order.reserved_cny > 0 else None
        return Preview(
            price_bound=order.price_bound,
            estimated_fee=money(order.price_bound * order.quantity * FEE),
            reserved_cny=order.reserved_cny,
            reserved_foreign=order.reserved_foreign,
            fx_rate=conversion_rate.cny_per_unit if conversion_rate else None,
            fx_source=conversion_rate.source if conversion_rate else None,
            fx_time=conversion_rate.market_time if conversion_rate else None,
            expires_at=order.expires_at,
            warnings=["模拟费用 0.05%；整笔成交；行情可能延迟；公司行动需人工核对"],
        )

    def place(self, request: OrderInput, now: datetime) -> Order:
        """以请求编号保证委托受理幂等。"""
        with self.store.transaction() as connection:
            saved = retry_order(connection, request)
            if saved is not None:
                return saved
            round_id = self.store.load(connection).id
        quote, rate, reference = self.prepare(request, now)
        with self.store.transaction() as connection:
            saved = retry_order(connection, request)
            if saved is not None:
                return saved
            state = self.store.load(connection)
            if state.id != round_id:
                raise DomainError("round_changed", "练习轮次已变化，请刷新后重新操作")
            check_actions(connection, state.id, instrument(request.symbol, self.catalog), quote, now)
            order = build_order(
                instrument(request.symbol, self.catalog),
                state,
                request,
                OrderContext(quote, rate, now, reference),
            )
            snapshot = order_snapshot(connection, state, request)
            order = order.model_copy(
                update={"plan_id": request.plan_id, "plan_version": request.plan_version, "plan_snapshot": snapshot}
            )
            state.orders.append(order)
            self.store.save(connection, state)
            _ = connection.execute(
                "INSERT INTO requests VALUES(?,?,?)",
                (
                    request.request_id,
                    request.model_dump_json(),
                    order.model_dump_json(),
                ),
            )
            return order

    def cancel(self, order_id: str, now: datetime) -> Order:
        """记录撤单截止，延迟确认前保持资源占用。"""
        with self.store.transaction() as connection:
            state = self.store.load(connection)
            for index, original in enumerate(state.orders):
                if original.id == order_id:
                    order = original
                    if order.status == "accepted":
                        waiting = now > order.active_at
                        order = order.model_copy(
                            update={
                                "cancel_at": now,
                                "status": "cancel_pending" if waiting else "cancelled",
                                "status_reason": "等待撤单截止前的延迟样本确认" if waiting else "预挂委托已撤销",
                            }
                        )
                        state.orders[index] = order
                        self.store.save(connection, state)
                    return order
            raise DomainError("order_missing", "委托不存在", 404)

    def process(self, symbol: str, samples: tuple[Quote, ...], now: datetime) -> None:
        """按市场时间处理新增样本，结束状态不可回填。"""
        item = instrument(symbol, self.catalog)
        if not item.tradable:
            samples = ()
        with self.store.transaction() as connection:
            state = self.store.load(connection)
            for index, original in enumerate(state.orders):
                if original.symbol != symbol or original.status not in ACTIVE:
                    continue
                order = original
                cutoff = min(order.expires_at, order.cancel_at or order.expires_at)
                for sample in sorted(samples, key=lambda quote: quote.market_time):
                    order = advance(state, order, item, sample, now)
                    if order.status not in ACTIVE:
                        break
                if order.status in ACTIVE and now > cutoff + timedelta(minutes=20):
                    order = order.model_copy(
                        update={
                            "status": "terminated",
                            "status_reason": "数据不足，未确认成交；释放占用，不回填迟到数据",
                            "confirmed_at": now,
                        }
                    )
                state.orders[index] = order
            self.store.save(connection, state)
