"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Response

from app.activated_registry import RuleProvider
from app.api_models import (
    HistoryView,
    Ok,
    QuoteView,
    SymbolInput,
)
from app.auth import Auth
from app.discovery import Discovery, persist_observation
from app.discovery_models import ObservationInstrument, SearchResult, merge_search, symbol_identity
from app.engine import Engine
from app.market_session import project
from app.market_session_models import Market, MarketSessionView
from app.marketdata_models import Bar, Quote
from app.models import (
    DomainError,
    Instrument,
)
from app.order_rules import tick_size
from app.storage import read_rows
from app.views import CLOSE_BUFFER, Change, bar_complete, quote_change


def market_session(market: Market, response: Response) -> MarketSessionView:
    """独立查询市场支持时段，不访问证券、行情或账本。"""
    response.headers["Cache-Control"] = "no-store"
    return project(market, datetime.now(UTC))


def _reference(
    item: Instrument | ObservationInstrument, engine: Engine, discovery: Discovery, display: RuleProvider | None
) -> tuple[Quote, Change]:
    """展示读取源存在时，价格与涨跌全部出自同一次报价响应；报价本身失败照旧抛出。"""
    if isinstance(item, ObservationInstrument):
        reference = discovery.quote_reference(item)
    elif display is not None:
        reference = display.quote_reference(item.symbol)
    else:
        return engine.provider.quote(item.symbol), Change()
    return reference.quote, quote_change(item.market, reference)


def _bars(
    item: Instrument | ObservationInstrument, engine: Engine, discovery: Discovery, display: RuleProvider | None
) -> tuple[tuple[Bar, ...], bool]:
    """图表优先使用约一年日线；未接展示源时保持原 30 条且不标注定稿状态。"""
    if isinstance(item, ObservationInstrument):
        return discovery.chart_history(item), True
    if display is not None:
        return display.chart_history(item.symbol), True
    return engine.provider.history(item.symbol), False


def _catalog_instrument(symbol: str, engine: Engine) -> Instrument | None:
    """按已有证券代码规范精确匹配当前交易目录。"""
    return next((item for item in engine.catalog if symbol_identity(item.symbol) == symbol_identity(symbol)), None)


def _local_instrument(symbol: str, engine: Engine) -> Instrument | ObservationInstrument:
    """只查询当前规则和持久化观察身份，不请求行情源。"""
    item = _catalog_instrument(symbol, engine)
    if item is not None:
        return item
    with engine.store.transaction() as connection:
        rows = read_rows(connection, "SELECT data FROM observations WHERE symbol=?", (symbol,))
    if rows:
        return ObservationInstrument.model_validate_json(rows[0][0])
    raise DomainError("unknown_instrument", "本地不存在该证券", 404)


def _register_instruments(app: FastAPI, engine: Engine, auth: Auth, discovery: Discovery) -> None:
    """注册证券目录、搜索及本地精确身份接口。"""

    @app.get("/api/instruments/search", dependencies=[Depends(auth.require)])
    def search(q: Annotated[str, Query(max_length=80)] = "") -> SearchResult:
        return merge_search(q, engine.catalog, discovery.search(q))

    @app.get("/api/instruments", dependencies=[Depends(auth.require)])
    def instruments(q: str = "") -> list[Instrument]:
        return [i for i in engine.catalog if q.lower() in (i.symbol + i.name + i.market + i.kind).lower()]

    @app.get("/api/instruments/{symbol}", dependencies=[Depends(auth.require)])
    def local_instrument(symbol: str) -> Instrument | ObservationInstrument:
        """读取本地证券身份，包括移出自选后仍持久保存的观察标的。"""
        return _local_instrument(symbol, engine)


def register(app: FastAPI, engine: Engine, auth: Auth, display: RuleProvider | None = None) -> None:
    """注册本模块的个人账户 HTTP 接口。"""
    discovery = Discovery()

    def lookup(symbol: str) -> Instrument | ObservationInstrument:
        return _catalog_instrument(symbol, engine) or discovery.lookup(symbol, engine.store)

    _ = app.get("/api/markets/{market}/session", dependencies=[Depends(auth.require)])(market_session)

    _register_instruments(app, engine, auth, discovery)

    @app.get("/api/instruments/{symbol}/quote", dependencies=[Depends(auth.require)])
    def quote(symbol: str) -> QuoteView:
        """返回带来源与市场时间的估值快照。"""
        item = lookup(symbol)
        data, change = _reference(item, engine, discovery, display)
        return QuoteView(
            symbol=item.symbol,
            price=data.price,
            tick_size=None if isinstance(item, ObservationInstrument) else tick_size(item, data.price),
            currency=data.currency,
            market_time=data.market_time,
            retrieved_at=data.retrieved_at,
            source=data.source,
            status="reference",
            message="估值快照；成交使用独立完整分钟线" if item.tradable else item.restriction,
            previous_close=change.previous_close,
            change=change.change,
            change_percent=change.change_percent,
            change_day=change.change_day,
            change_base_day=change.change_base_day,
            change_status=change.change_status,
        )

    @app.get("/api/instruments/{symbol}/history", dependencies=[Depends(auth.require)])
    def history(symbol: str) -> list[HistoryView]:
        """返回仅供绘图的未复权日线；展示读取源存在时为约一年并标注取得时刻与是否定稿。"""
        item = lookup(symbol)
        bars, labelled = _bars(item, engine, discovery, display)
        return [
            HistoryView(
                time=b.day.isoformat(),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                retrieved_at=b.retrieved_at if labelled else None,
                complete=bar_complete(item.market, b.day, b.retrieved_at, CLOSE_BUFFER[item.market])
                if labelled
                else None,
            )
            for b in bars
        ]

    @app.get("/api/watchlist", dependencies=[Depends(auth.require)])
    def watchlist() -> list[Instrument | ObservationInstrument]:
        return [lookup(s) for s in engine.store.read().watchlist]

    @app.post("/api/watchlist", dependencies=[Depends(auth.require)])
    def add_watch(body: SymbolInput) -> Ok:
        item = lookup(body.symbol)
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            persist_observation(connection, item)
            if item.symbol not in state.watchlist:
                state.watchlist.append(item.symbol)
            engine.store.save(connection, state)
        return Ok()

    @app.delete("/api/watchlist/{symbol}", dependencies=[Depends(auth.require)])
    def remove_watch(symbol: str) -> Ok:
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.watchlist[:] = [s for s in state.watchlist if s != symbol]
            engine.store.save(connection, state)
        return Ok()
