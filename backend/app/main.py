"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.activated_registry import RuleProvider
from app.admission_routes import register as register_admission_routes
from app.analysis_routes import register as register_analysis_routes
from app.archive_routes import register as register_archive_routes
from app.asset_routes import register as register_asset_routes
from app.auth import Auth
from app.auth_routes import register as register_auth_routes
from app.catalog import instrument
from app.engine import Engine
from app.market_routes import register as register_market_routes
from app.marketdata import MarketDataClient, MarketDataError
from app.models import (
    DomainError,
)
from app.personal_routes import register as register_personal_routes
from app.plan_routes import register as register_plan_routes
from app.plan_worker import PlanWorker
from app.record_routes import register as register_record_routes
from app.review_routes import register as register_review_routes
from app.security import AccessConfig
from app.security import register as register_security
from app.storage import Store
from app.trading_routes import register as register_trading_routes
from app.worker import Worker


def configured_engine() -> Engine:
    """从本地环境配置建立真实行情与可人工暂停的标的清单。"""
    paused = frozenset(filter(None, os.getenv("PAPER_PAUSED_SYMBOLS", "").split(",")))
    client = MarketDataClient()
    engine = Engine(Store(Path(os.getenv("PAPER_DB", "data/paper.sqlite3"))), client)
    for symbol in paused:
        _ = instrument(symbol, engine.catalog)
    engine.paused = paused
    engine.provider = RuleProvider(engine.registry, client)
    engine.plan_provider = RuleProvider(engine.registry, MarketDataClient())
    return engine


def create_app(
    engine: Engine | None = None,
    password: str | None = None,
    *,
    run_worker: bool = True,
) -> FastAPI:
    """创建个人应用；测试通过构造依赖隔离外部行情。"""
    engine = engine or configured_engine()
    auth = Auth(
        password if password is not None else os.getenv("PAPER_PASSWORD", ""),
        secure=os.getenv("PAPER_SECURE_COOKIE") == "1",
    )
    worker = Worker(engine)
    plan_worker = PlanWorker(engine)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as group:
            if run_worker:
                _ = group.start_soon(worker.run)
                _ = group.start_soon(plan_worker.run)
            yield
            group.cancel_scope.cancel()

    app = FastAPI(title="个人模拟投资", version="0.4.7", lifespan=lifespan)
    app.state.engine = engine
    app.state.worker = worker
    app.state.plan_worker = plan_worker

    register_security(app, AccessConfig.from_env())

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            {"detail": {"code": error.code, "message": error.message}},
            status_code=error.status,
        )

    @app.exception_handler(MarketDataError)
    async def market_error(_: Request, error: MarketDataError) -> JSONResponse:
        return JSONResponse(
            {
                "detail": {
                    "code": error.code,
                    "message": "外部行情暂不可用，未使用替代假数据",
                }
            },
            status_code=503,
        )

    register_auth_routes(app, auth)
    register_market_routes(app, engine, auth)
    register_admission_routes(app, engine, auth)
    register_trading_routes(app, engine, auth)
    register_record_routes(app, engine, auth)
    register_asset_routes(app, engine, auth, worker, plan_worker)
    register_personal_routes(app, engine, auth)
    register_plan_routes(app, engine, auth)
    register_review_routes(app, engine, auth)
    register_archive_routes(app, engine, auth)
    register_analysis_routes(app, engine, auth)

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
