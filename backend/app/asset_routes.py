"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

from datetime import UTC, datetime

from fastapi import Depends, FastAPI

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
from app.views import portfolio
from app.worker import Worker


def register(app: FastAPI, engine: Engine, auth: Auth, worker: Worker, plan_worker: PlanWorker) -> None:
    """注册本模块的个人账户 HTTP 接口。"""

    @app.get("/api/health", dependencies=[Depends(auth.require)])
    def health() -> HealthView:
        return HealthView(last_run=worker.view.last_run, status=worker.view.status, plan_worker=plan_worker.view)

    @app.get("/api/portfolio", dependencies=[Depends(auth.require)])
    def account() -> Portfolio:
        return portfolio(engine, datetime.now(UTC), worker.view)

    @app.get("/api/fx", dependencies=[Depends(auth.require)])
    def fx() -> tuple[FxRate, ...]:
        return engine.provider.fx_rates()

    @app.post("/api/fx/convert", dependencies=[Depends(auth.require)])
    def exchange(body: ConvertInput) -> Portfolio:
        convert(engine, body, datetime.now(UTC))
        return account()
