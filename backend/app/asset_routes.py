"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

import logging
import sqlite3
from datetime import UTC, datetime

from fastapi import Depends, FastAPI

from app.activated_registry import RuleProvider
from app.api_models import (
    ConvertInput,
)
from app.auth import Auth
from app.engine import Engine
from app.marketdata import FxRate
from app.models import (
    HealthView,
    Portfolio,
)
from app.operations import convert
from app.plan_worker import PlanWorker
from app.views import ValuationCache, portfolio
from app.worker import Worker, gap_summary


def register(  # noqa: PLR0913 展示读取源必须显式传入，不从 engine.provider 隐式推断。
    app: FastAPI,
    engine: Engine,
    auth: Auth,
    worker: Worker,
    plan_worker: PlanWorker,
    *,
    cache: ValuationCache,
    display: RuleProvider | None = None,
) -> None:
    """注册本模块的个人账户 HTTP 接口。"""

    @app.get("/api/health", dependencies=[Depends(auth.require)])
    def health() -> HealthView:
        try:
            count, gaps = gap_summary(engine.store, datetime.now(UTC))
        except (sqlite3.Error, ValueError) as error:
            # 数据库暂不可读时仍返回内存中的后台状态，中断记录留待下次读取。
            logging.getLogger(__name__).warning("后台中断记录读取失败 type=%s", type(error).__name__)
            count, gaps = 0, []
        return HealthView(
            last_run=worker.view.last_run,
            status=worker.view.status,
            plan_worker=plan_worker.view,
            recent_gaps=gaps,
            gap_count_7d=count,
        )

    @app.get("/api/portfolio", dependencies=[Depends(auth.require)])
    def account() -> Portfolio:
        return portfolio(engine, datetime.now(UTC), worker.view, display, cache=cache)

    @app.get("/api/fx", dependencies=[Depends(auth.require)])
    def fx() -> tuple[FxRate, ...]:
        return engine.provider.fx_rates()

    @app.post("/api/fx/convert", dependencies=[Depends(auth.require)])
    def exchange(body: ConvertInput) -> Portfolio:
        convert(engine, body, datetime.now(UTC))
        return account()
