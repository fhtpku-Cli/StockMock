"""资产分析只读接口，当前与归档使用同一计算入口。"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Response

from app.analysis_models import CashStatement, LedgerPage, LedgerQuery, SnapshotQuery, SnapshotRange
from app.auth import Auth
from app.cash_queries import filtered_csv, ledger_page, statement
from app.engine import Engine
from app.snapshot_queries import snapshot_csv, snapshot_range


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """所有查询保留个人鉴权，不产生账本写入。"""

    @app.get("/api/ledger/page", dependencies=[Depends(auth.require)])
    def current_ledger(query: Annotated[LedgerQuery, Query()]) -> LedgerPage:
        return ledger_page(engine.store.read(), query)

    @app.get("/api/cash-statement", dependencies=[Depends(auth.require)])
    def current_statement(query: Annotated[LedgerQuery, Query()]) -> CashStatement:
        return statement(engine.store.read(), query)

    @app.get("/api/rounds/{round_id}/cash-statement", dependencies=[Depends(auth.require)])
    def archived_statement(round_id: UUID, query: Annotated[LedgerQuery, Query()]) -> CashStatement:
        return statement(engine.store.read_round(str(round_id)), query)

    @app.get("/api/ledger/export.csv", dependencies=[Depends(auth.require)])
    def current_export(query: Annotated[LedgerQuery, Query()]) -> Response:
        return filtered_csv(engine.store.read(), query)

    @app.get("/api/rounds/{round_id}/ledger/export.csv", dependencies=[Depends(auth.require)])
    def archived_export(round_id: UUID, query: Annotated[LedgerQuery, Query()]) -> Response:
        return filtered_csv(engine.store.read_round(str(round_id)), query)

    @app.get("/api/snapshots/range", dependencies=[Depends(auth.require)])
    def current_range(query: Annotated[SnapshotQuery, Query()]) -> SnapshotRange:
        return snapshot_range(engine.store, engine.store.read(), query, datetime.now(UTC))

    @app.get("/api/rounds/{round_id}/snapshots/range", dependencies=[Depends(auth.require)])
    def archived_range(round_id: UUID, query: Annotated[SnapshotQuery, Query()]) -> SnapshotRange:
        return snapshot_range(engine.store, engine.store.read_round(str(round_id)), query, datetime.now(UTC))

    @app.get("/api/snapshots/export.csv", dependencies=[Depends(auth.require)])
    def current_snapshot_export(query: Annotated[SnapshotQuery, Query()]) -> Response:
        return snapshot_csv(current_range(query))

    @app.get("/api/rounds/{round_id}/snapshots/export.csv", dependencies=[Depends(auth.require)])
    def archived_snapshot_export(round_id: UUID, query: Annotated[SnapshotQuery, Query()]) -> Response:
        return snapshot_csv(archived_range(round_id, query))
