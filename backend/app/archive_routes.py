"""归档历史完整分页查询，不修改原记录。"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query

from app.auth import Auth
from app.engine import Engine
from app.models import Entry, Journal, Order
from app.plan_models import Page
from app.plan_queries import PageQuery, page


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """提供超过旧详情五百条的完整访问路径。"""

    @app.get("/api/rounds/{round_id}/orders", dependencies=[Depends(auth.require)])
    def round_orders(round_id: UUID, query: Annotated[PageQuery, Query()]) -> Page[Order]:
        return page(list(reversed(engine.store.read_round(str(round_id)).orders)), query)

    @app.get("/api/rounds/{round_id}/journal", dependencies=[Depends(auth.require)])
    def round_journal(round_id: UUID, query: Annotated[PageQuery, Query()]) -> Page[Journal]:
        return page(list(reversed(engine.store.read_round(str(round_id)).journal)), query)

    @app.get("/api/rounds/{round_id}/ledger", dependencies=[Depends(auth.require)])
    def round_ledger(round_id: UUID, query: Annotated[PageQuery, Query()]) -> Page[Entry]:
        return page(list(reversed(engine.store.read_round(str(round_id)).ledger)), query)
