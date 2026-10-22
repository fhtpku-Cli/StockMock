"""启动：uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000。"""

import csv
import io
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Response

from app.api_models import (
    NoteInput,
    ResetInput,
    RoundDetail,
    RoundResult,
    RoundView,
)
from app.auth import Auth
from app.engine import Engine
from app.models import (
    Entry,
    Journal,
    State,
)
from app.operations import reset
from app.plan_exports import safe_cell
from app.plan_reviews import attach_note
from app.plans import load_plan
from app.storage import read_rows

DETAIL_LIMIT: Final = 500


def ledger_csv(state: State) -> Response:
    """导出完整流水，不受页面只读详情条数上限影响。"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["编号", "类型", "币种", "金额", "确认时间", "委托编号", "说明"])
    for row in state.ledger:
        note = safe_cell(row.note)
        writer.writerow(
            [
                row.id,
                row.kind,
                row.currency,
                row.amount,
                row.created_at.isoformat(),
                row.order_id,
                note,
            ]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="ledger.csv"'},
    )


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """注册本模块的个人账户 HTTP 接口。"""

    @app.get("/api/journal", dependencies=[Depends(auth.require)])
    def journal() -> list[Journal]:
        return list(reversed(engine.store.read().journal))

    @app.post("/api/journal", dependencies=[Depends(auth.require)])
    def add_note(body: NoteInput) -> Journal:
        note = Journal(
            id=str(uuid4()),
            text=body.text,
            order_id=body.order_id,
            plan_id=body.plan_id,
            created_at=datetime.now(UTC),
        )
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            plan = load_plan(connection, body.plan_id) if body.plan_id else None
            attach_note(state, note, plan)
            engine.store.save(connection, state)
        return note

    @app.get("/api/ledger", dependencies=[Depends(auth.require)])
    def ledger() -> list[Entry]:
        return list(reversed(engine.store.read().ledger))

    @app.get("/api/rounds", dependencies=[Depends(auth.require)])
    def rounds() -> list[RoundView]:
        with engine.store.transaction() as connection:
            states = [
                State.model_validate_json(row[0])
                for row in read_rows(connection, "SELECT data FROM rounds ORDER BY rowid DESC")
            ]
        return [RoundView(id=s.id, created_at=s.created_at, archived_at=s.archived_at) for s in states]

    @app.post("/api/rounds/reset", dependencies=[Depends(auth.require)])
    def reset_round(body: ResetInput) -> RoundResult:
        return reset(engine, body, datetime.now(UTC))

    @app.get("/api/export.csv", dependencies=[Depends(auth.require)])
    def export() -> Response:
        return ledger_csv(engine.store.read())

    @app.get("/api/rounds/{round_id}", dependencies=[Depends(auth.require)])
    def round_detail(round_id: UUID) -> RoundDetail:
        state = engine.store.read_round(str(round_id))
        return RoundDetail(
            id=state.id,
            created_at=state.created_at,
            archived_at=state.archived_at,
            cash=state.cash,
            orders=list(reversed(state.orders[-DETAIL_LIMIT:])),
            journal=list(reversed(state.journal[-DETAIL_LIMIT:])),
            ledger=list(reversed(state.ledger[-DETAIL_LIMIT:])),
            truncated=any(len(items) > DETAIL_LIMIT for items in (state.orders, state.journal, state.ledger)),
        )

    @app.get("/api/rounds/{round_id}/export.csv", dependencies=[Depends(auth.require)])
    def archived_export(round_id: UUID) -> Response:
        return ledger_csv(engine.store.read_round(str(round_id)))
