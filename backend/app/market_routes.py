"""启动：uv run uvicorn app.main:app --host 127.0.0.1 --port 8000。"""

from fastapi import Depends, FastAPI

from app.api_models import (
    HistoryView,
    Ok,
    QuoteView,
    SymbolInput,
)
from app.auth import Auth
from app.catalog import instrument
from app.engine import Engine
from app.models import (
    Instrument,
)


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """注册本模块的个人账户 HTTP 接口。"""

    @app.get("/api/instruments", dependencies=[Depends(auth.require)])
    def instruments(q: str = "") -> list[Instrument]:
        return [i for i in engine.catalog if q.lower() in (i.symbol + i.name + i.market + i.kind).lower()]

    @app.get("/api/instruments/{symbol}/quote", dependencies=[Depends(auth.require)])
    def quote(symbol: str) -> QuoteView:
        """返回带来源与市场时间的估值快照。"""
        item = instrument(symbol, engine.catalog)
        data = engine.provider.quote(symbol)
        return QuoteView(
            symbol=symbol,
            price=data.price,
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
        _ = instrument(symbol, engine.catalog)
        return [
            HistoryView(
                time=b.day.isoformat(),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in engine.provider.history(symbol)
        ]

    @app.get("/api/watchlist", dependencies=[Depends(auth.require)])
    def watchlist() -> list[Instrument]:
        return [instrument(s, engine.catalog) for s in engine.store.read().watchlist]

    @app.post("/api/watchlist", dependencies=[Depends(auth.require)])
    def add_watch(body: SymbolInput) -> Ok:
        _ = instrument(body.symbol, engine.catalog)
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            if body.symbol not in state.watchlist:
                state.watchlist.append(body.symbol)
            engine.store.save(connection, state)
        return Ok()

    @app.delete("/api/watchlist/{symbol}", dependencies=[Depends(auth.require)])
    def remove_watch(symbol: str) -> Ok:
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.watchlist[:] = [s for s in state.watchlist if s != symbol]
            engine.store.save(connection, state)
        return Ok()
