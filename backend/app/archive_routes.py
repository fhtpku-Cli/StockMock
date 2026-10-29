"""归档历史完整分页查询，不修改原记录。"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query

from app.analysis_models import LedgerPage, LedgerQuery
from app.auth import Auth
from app.cash_queries import ledger_page
from app.engine import Engine
from app.models import Journal, OrderView
from app.order_views import order_view
from app.plan_models import Page
from app.plan_queries import PageQuery, page
from app.review_journal import journal_page
from app.review_queries import RecordQuery
from app.review_repository import read_context


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """提供超过旧详情五百条的完整访问路径。"""

    @app.get("/api/rounds/{round_id}/orders", dependencies=[Depends(auth.require)])
    def round_orders(round_id: UUID, query: Annotated[PageQuery, Query()]) -> Page[OrderView]:
        return page(
            [order_view(o, engine.catalog) for o in reversed(engine.store.read_round(str(round_id)).orders)], query
        )

    @app.get("/api/rounds/{round_id}/journal", dependencies=[Depends(auth.require)])
    def round_journal(round_id: UUID, query: Annotated[RecordQuery, Query()]) -> Page[Journal]:
        result = journal_page(read_context(engine.store, str(round_id)), query)
        return Page[Journal](
            items=[Journal.model_validate(n.model_dump(include=set(Journal.model_fields))) for n in result.items],
            total=result.total,
            offset=result.offset,
            limit=result.limit,
        )

    @app.get("/api/rounds/{round_id}/ledger", dependencies=[Depends(auth.require)])
    def round_ledger(round_id: UUID, query: Annotated[LedgerQuery, Query()]) -> LedgerPage:
        return ledger_page(engine.store.read_round(str(round_id)), query)
