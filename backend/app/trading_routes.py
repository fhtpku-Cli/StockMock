"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Query

from app.accounting import ACTIVE, available, money
from app.auth import Auth
from app.catalog import instrument
from app.composition import percentage, rounded
from app.engine import Engine, PreviewContext
from app.episodes import PositionEpisodes, position_episodes
from app.models import (
    DomainError,
    Order,
    OrderInput,
    OrderView,
    PositionImpact,
    Preview,
)
from app.order_endings import RecentEndings, recent_endings
from app.order_requests import OrderRejection, order_receipt, reject_order
from app.order_views import WaitingContext, order_view
from app.personal_models import Sizing
from app.sizing import size_order
from app.views import ValuationCache, ValuedState, holding_weight
from app.worker import Worker


def place_order(engine: Engine, body: OrderInput, now: datetime, *, retry: bool) -> Order:
    """恢复重试遇到业务拒绝时，在短事务中保存唯一最终结果。"""
    try:
        order = engine.place(body, now)
    except DomainError as error:
        if not retry or error.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise
        rejection = OrderRejection(code=error.code, message=error.message)
        with engine.store.transaction() as connection:
            order = reject_order(connection, body, rejection)
        if order is None:
            raise rejection.error() from error
    return order


RateSource = Literal["估值汇率", "预览汇率", "人民币原币"]


def preview_rate(
    context: PreviewContext, cached: ValuedState | None
) -> tuple[Decimal | None, RateSource | None, datetime | None]:
    """优先使用账户估值汇率，首次买入外币证券时可使用同次预览取得的汇率。"""
    currency = context.quote.currency
    if currency == "CNY":
        return Decimal(1), "人民币原币", None
    if cached and currency in cached.valuation.rates:
        return cached.valuation.rates[currency], "估值汇率", cached.valuation.times.get(f"FX:{currency}")
    if context.rate and context.rate.cny_per_unit > 0:
        return context.rate.cny_per_unit, "预览汇率", context.rate.market_time
    return None, None, None


def estimate_position(context: PreviewContext, request: OrderInput, cached: ValuedState | None) -> Preview:
    """仅使用已经取到的数据拼装预览；估值缺失时保留原有预览和受理能力。"""
    preview, quote, state = context.preview, context.quote, context.state
    rate, source, fx_time = preview_rate(context, cached)
    total = cached.valuation.total if cached else None
    held = sum(lot.quantity for lot in state.lots if lot.symbol == request.symbol)
    impact = PositionImpact(
        status="expired" if cached is None else ("missing_fx" if rate is None else "incomplete"),
        quote_time=quote.market_time,
        valuation_time=cached.at if cached else None,
        fx_source=source,
        fx_time=fx_time,
        active_orders=sum(order.status in ACTIVE for order in state.orders),
    )
    stop_loss = preview.stop_loss
    if stop_loss is not None and rate is not None:
        cny = money(stop_loss.amount * rate)
        stop_loss = stop_loss.model_copy(update={"cny": cny, "asset_percent": rounded(percentage(cny, total))})
    if cached is not None and total is not None and total > 0 and rate is not None:
        price = cached.valuation.prices.get(request.symbol) if held else quote.price
        if price is not None:
            direction = 1 if request.side == "buy" else -1
            cash_price = preview.price_bound if request.order_type == "limit" else quote.price
            gross = money(request.quantity * cash_price)
            cash_delta = money((-direction * gross - preview.estimated_fee) * rate)
            value_delta = money(money(request.quantity * price) * rate)
            after_total = total + cash_delta + direction * value_delta
            current = holding_weight(money(held * price), rate, total)
            after = holding_weight(money((held + direction * request.quantity) * price), rate, after_total)
            if after is not None:
                impact = impact.model_copy(
                    update={
                        "status": "complete",
                        "current_percent": rounded(current),
                        "after_percent": rounded(after),
                        "quote_time": cached.valuation.times[request.symbol] if held else quote.market_time,
                    }
                )
    if impact.status != "complete":
        if request.side == "sell":
            impact = impact.model_copy(update={"remaining_quantity": held - request.quantity})
        else:
            impact = impact.model_copy(
                update={
                    "reserved_cny_percent": rounded(percentage(preview.reserved_cny, available(state, "CNY")))
                    if preview.reserved_cny
                    else None,
                    "reserved_foreign_percent": rounded(
                        percentage(preview.reserved_foreign, available(state, quote.currency))
                    )
                    if preview.reserved_foreign
                    else None,
                }
            )
    return preview.model_copy(update={"position_impact": impact, "stop_loss": stop_loss})


def register_episode_routes(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """集中注册持仓回看，避免增加交易路由函数的嵌套复杂度。"""

    @app.get("/api/positions/{symbol}/episodes", dependencies=[Depends(auth.require)])
    def episodes(symbol: str) -> PositionEpisodes:
        """只读当前轮次单只证券的建仓、清仓与公司行动时间线。"""
        item = instrument(symbol, engine.catalog)
        return position_episodes(engine.store, item, datetime.now(UTC))


def register(app: FastAPI, engine: Engine, auth: Auth, worker: Worker, cache: ValuationCache) -> None:
    """注册本模块的个人账户 HTTP 接口。"""
    register_episode_routes(app, engine, auth)

    @app.post("/api/orders/preview", dependencies=[Depends(auth.require)])
    def preview(body: OrderInput) -> Preview:
        """检查委托资源并返回预览，不改变账户。"""
        context = engine.preview_with_context(body, datetime.now(UTC))
        return estimate_position(context, body, cache.match(context.state, datetime.now(UTC)))

    @app.post("/api/orders/size", dependencies=[Depends(auth.require)])
    def sizing(body: OrderInput, percent: Annotated[int, Query(ge=1, le=100)] = 100) -> Sizing:
        if percent not in {25, 50, 100}:
            raise DomainError("invalid_percent", "仓位比例仅支持 25、50 或 100", 422)
        return size_order(engine, body, datetime.now(UTC), percent)

    @app.post("/api/orders", dependencies=[Depends(auth.require)])
    def place(body: OrderInput, retry: Annotated[str | None, Header(alias="X-StockMock-Retry")] = None) -> OrderView:
        """以请求编号保证委托受理幂等。"""
        now = datetime.now(UTC)
        order = place_order(engine, body, now, retry=retry == "1")
        return order_view(order, engine.catalog, waiting=WaitingContext(now, engine.paused, worker.failures))

    @app.get("/api/orders", dependencies=[Depends(auth.require)])
    def orders() -> list[OrderView]:
        context = WaitingContext(datetime.now(UTC), engine.paused, worker.failures)
        return [order_view(o, engine.catalog, waiting=context) for o in reversed(engine.store.read().orders)]

    @app.get("/api/orders/recent-endings", dependencies=[Depends(auth.require)])
    def endings() -> RecentEndings:
        """只读当前轮次最近 3 个北京自然日内结束的委托。"""
        return recent_endings(engine.store.read(), engine.catalog, datetime.now(UTC))

    @app.get("/api/orders/receipts/{request_id}", dependencies=[Depends(auth.require)])
    def receipt(request_id: str) -> OrderView:
        """只读核对原编号的受理结果，包括已经归档的委托。"""
        with closing(sqlite3.connect(f"{engine.store.path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            _ = connection.execute("BEGIN")
            order = order_receipt(connection, request_id)
        if order is None:
            raise DomainError("order_receipt_missing", "尚未查询到此编号的已受理委托", 404)
        context = WaitingContext(datetime.now(UTC), engine.paused, worker.failures)
        return order_view(order, engine.catalog, waiting=context)

    @app.post("/api/orders/{order_id}/cancel", dependencies=[Depends(auth.require)])
    def cancel(order_id: str) -> OrderView:
        """记录撤单截止，延迟确认前保持资源占用。"""
        now = datetime.now(UTC)
        return order_view(
            engine.cancel(order_id, now), engine.catalog, waiting=WaitingContext(now, engine.paused, worker.failures)
        )
