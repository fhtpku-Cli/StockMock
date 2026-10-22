"""计划、站内提示及复盘的鉴权 HTTP 接口。"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Response

from app.auth import Auth
from app.engine import Engine
from app.models import Order, State
from app.plan_exports import export_plans, export_reviews
from app.plan_models import Page, Plan, PlanInput, PlanUpdate
from app.plan_queries import PageQuery, PlanQuery, page
from app.plans import create_plan, get_plan, round_plans, update_plan


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """所有写入只作用于当前轮次，归档通过相同读接口查看。"""

    def state_for(round_id: str | None) -> State:
        return engine.store.read_round(round_id) if round_id else engine.store.read()

    @app.get("/api/plans/export.csv", dependencies=[Depends(auth.require)])
    def plans_export(round_id: str | None = None) -> Response:
        return export_plans(round_plans(engine.store, state_for(round_id).id))

    @app.get("/api/reviews/export.csv", dependencies=[Depends(auth.require)])
    def reviews_export(round_id: str | None = None) -> Response:
        return export_reviews(engine.store, state_for(round_id))

    @app.get("/api/plans", dependencies=[Depends(auth.require)])
    def list_plans(query: Annotated[PlanQuery, Query()]) -> Page[Plan]:
        items = [
            p
            for p in round_plans(engine.store, state_for(query.round_id).id)
            if (query.symbol is None or p.symbol == query.symbol) and (query.status is None or p.status == query.status)
        ]
        return page(items, query)

    @app.post("/api/plans", dependencies=[Depends(auth.require)])
    def create(body: PlanInput) -> Plan:
        return create_plan(engine, body, datetime.now(UTC))

    @app.get("/api/plans/{plan_id}", dependencies=[Depends(auth.require)])
    def detail(plan_id: UUID) -> Plan:
        return get_plan(engine.store, str(plan_id))

    @app.put("/api/plans/{plan_id}", dependencies=[Depends(auth.require)])
    def update(plan_id: UUID, body: PlanUpdate) -> Plan:
        return update_plan(engine, str(plan_id), body)

    @app.get("/api/plans/{plan_id}/orders", dependencies=[Depends(auth.require)])
    def orders(plan_id: UUID, query: Annotated[PageQuery, Query()]) -> Page[Order]:
        plan = get_plan(engine.store, str(plan_id))
        return page([o for o in reversed(state_for(plan.round_id).orders) if o.plan_id == plan.id], query)
