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
from app.plan_queries import AlertQuery, ReviewQuery, page
from app.plan_reviews import reviews


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """将提示和复盘筛选保持为独立的 HTTP 责任。"""

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
