"""站内提示和复盘列表的读取接口。"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Response

from app.auth import Auth
from app.engine import Engine
from app.models import State
from app.plan_alerts import alerts, read_alert
from app.plan_exports import export_alerts
from app.plan_models import Alert, AlertPage, Page, Review
from app.plan_queries import AlertQuery, PageQuery, ReviewQuery, page
from app.plan_reviews import reviews
from app.review_conclusions import detail, save_conclusion
from app.review_core_models import ConclusionInput, SubjectType
from app.review_facts import facts
from app.review_journal import journal_page
from app.review_models import ConclusionResult, FactPage, JournalPage, ReviewDetail, ReviewPage
from app.review_queries import RecordQuery, WorkbenchQuery, workbench
from app.review_repository import read_context


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """将提示和复盘筛选保持为独立的 HTTP 责任。"""
    register_workbench(app, engine, auth)

    def state_for(round_id: str | None) -> State:
        return engine.store.read_round(round_id) if round_id else engine.store.read()

    @app.get("/api/alerts/export.csv", dependencies=[Depends(auth.require)])
    def alerts_export(round_id: str | None = None) -> Response:
        return export_alerts(alerts(engine.store, state_for(round_id).id))

    @app.get("/api/alerts", dependencies=[Depends(auth.require)])
    def list_alerts(query: Annotated[AlertQuery, Query()]) -> AlertPage:
        items = [
            a
            for a in alerts(engine.store, state_for(query.round_id).id)
            if query.symbol is None or a.symbol == query.symbol
        ]
        unread = sum(a.read_at is None for a in items)
        result = page([a for a in items if not query.unread_only or a.read_at is None], query)
        return AlertPage(
            items=result.items, total=result.total, offset=result.offset, limit=result.limit, unread_count=unread
        )

    @app.post("/api/alerts/{alert_id}/read", dependencies=[Depends(auth.require)])
    def mark_read(alert_id: UUID) -> Alert:
        return read_alert(engine.store, str(alert_id), datetime.now(UTC))

    @app.get("/api/reviews", dependencies=[Depends(auth.require)])
    def list_reviews(query: Annotated[ReviewQuery, Query()]) -> Page[Review]:
        items = reviews(engine.store, state_for(query.round_id), datetime.now(UTC))
        return page([r for r in items if query.status in ("all", r.status)], query)


def register_workbench(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """注册新版复盘详情、笔记和工作台接口。"""

    @app.get("/api/reviews/page", dependencies=[Depends(auth.require)])
    def review_page(query: Annotated[WorkbenchQuery, Query()]) -> ReviewPage:
        return workbench(read_context(engine.store, query.round_id), query, datetime.now(UTC))

    @app.get("/api/journal/page", dependencies=[Depends(auth.require)])
    def notes_page(query: Annotated[RecordQuery, Query()]) -> JournalPage:
        return journal_page(read_context(engine.store, query.round_id), query)

    @app.get("/api/reviews/{subject_type}/{subject_id}", dependencies=[Depends(auth.require)])
    def review_detail(subject_type: SubjectType, subject_id: UUID, round_id: str | None = None) -> ReviewDetail:
        return detail(read_context(engine.store, round_id), (subject_type, str(subject_id)), datetime.now(UTC))

    @app.post("/api/reviews/{subject_type}/{subject_id}/conclusions", dependencies=[Depends(auth.require)])
    def conclude(subject_type: SubjectType, subject_id: UUID, body: ConclusionInput) -> ConclusionResult:
        return save_conclusion(engine.store, (subject_type, str(subject_id)), body)

    @app.get("/api/reviews/{subject_type}/{subject_id}/facts", dependencies=[Depends(auth.require)])
    def review_facts(
        subject_type: SubjectType, subject_id: UUID, query: Annotated[PageQuery, Query()], round_id: str | None = None
    ) -> FactPage:
        context = read_context(engine.store, round_id)
        result = page(facts(context, (subject_type, str(subject_id)), datetime.now(UTC)), query)
        return FactPage(
            items=result.items,
            total=result.total,
            offset=result.offset,
            limit=result.limit,
            round_id=context.state.id,
            read_only=context.state.archived_at is not None,
        )
