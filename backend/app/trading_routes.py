"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Query

from app.auth import Auth
from app.engine import Engine
from app.models import (
    DomainError,
    Order,
    OrderInput,
    Preview,
)
from app.personal_models import Sizing
from app.sizing import size_order


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """注册本模块的个人账户 HTTP 接口。"""

    @app.post("/api/orders/preview", dependencies=[Depends(auth.require)])
    def preview(body: OrderInput) -> Preview:
        """检查委托资源并返回预览，不改变账户。"""
        return engine.preview(body, datetime.now(UTC))

    @app.post("/api/orders/size", dependencies=[Depends(auth.require)])
    def sizing(body: OrderInput, percent: Annotated[int, Query(ge=1, le=100)] = 100) -> Sizing:
        if percent not in {25, 50, 100}:
            raise DomainError("invalid_percent", "仓位比例仅支持 25、50 或 100", 422)
        return size_order(engine, body, datetime.now(UTC), percent)

    @app.post("/api/orders", dependencies=[Depends(auth.require)])
    def place(body: OrderInput) -> Order:
        """以请求编号保证委托受理幂等。"""
        return engine.place(body, datetime.now(UTC))

    @app.get("/api/orders", dependencies=[Depends(auth.require)])
    def orders() -> list[Order]:
        return list(reversed(engine.store.read().orders))

    @app.post("/api/orders/{order_id}/cancel", dependencies=[Depends(auth.require)])
    def cancel(order_id: str) -> Order:
        """记录撤单截止，延迟确认前保持资源占用。"""
        return engine.cancel(order_id, datetime.now(UTC))
