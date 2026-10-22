"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

from typing import Annotated

from fastapi import Depends, FastAPI, Query

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
from app.models import (
    Instrument,
)
from app.order_rules import tick_size


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """注册本模块的个人账户 HTTP 接口。"""
    discovery = Discovery()

    def lookup(symbol: str) -> Instrument | ObservationInstrument:
        return next(
            (item for item in engine.catalog if symbol_identity(item.symbol) == symbol_identity(symbol)), None
        ) or discovery.lookup(symbol, engine.store)

    @app.get("/api/instruments/search", dependencies=[Depends(auth.require)])
    def search(q: Annotated[str, Query(max_length=80)] = "") -> SearchResult:
        return merge_search(q, engine.catalog, discovery.search(q))

    @app.get("/api/instruments", dependencies=[Depends(auth.require)])
    def instruments(q: str = "") -> list[Instrument]:
        return [i for i in engine.catalog if q.lower() in (i.symbol + i.name + i.market + i.kind).lower()]

    @app.get("/api/instruments/{symbol}/quote", dependencies=[Depends(auth.require)])
    def quote(symbol: str) -> QuoteView:
        """返回带来源与市场时间的估值快照。"""
        item = lookup(symbol)
        data = discovery.quote(item) if isinstance(item, ObservationInstrument) else engine.provider.quote(item.symbol)
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
        )

    @app.get("/api/instruments/{symbol}/history", dependencies=[Depends(auth.require)])
    def history(symbol: str) -> list[HistoryView]:
        """返回仅供绘图的未复权日线。"""
        item = lookup(symbol)
        bars = (
            discovery.history(item) if isinstance(item, ObservationInstrument) else engine.provider.history(item.symbol)
        )
        return [
            HistoryView(
                time=b.day.isoformat(),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
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
