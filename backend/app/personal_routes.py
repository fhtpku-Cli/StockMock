"""个人资产轨迹和人工公司行动的鉴权接口。"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, FastAPI

from app.adjustments import apply_action, receive_dividend, records
from app.auth import Auth
from app.engine import Engine
from app.personal_models import Adjustment, AdjustmentInput, PaymentInput, ValuationSnapshot
from app.snapshots import snapshots


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """归档仅提供读取接口，所有写入校验当前轮次。"""

    @app.get("/api/snapshots", dependencies=[Depends(auth.require)])
    def current_snapshots() -> list[ValuationSnapshot]:
        return snapshots(engine.store, engine.store.read().id)

    @app.get("/api/rounds/{round_id}/snapshots", dependencies=[Depends(auth.require)])
    def archived_snapshots(round_id: UUID) -> list[ValuationSnapshot]:
        return snapshots(engine.store, engine.store.read_round(str(round_id)).id)

    @app.get("/api/adjustments", dependencies=[Depends(auth.require)])
    def adjustments() -> list[Adjustment]:
        return records(engine.store, engine.store.read().id)

    @app.get("/api/rounds/{round_id}/adjustments", dependencies=[Depends(auth.require)])
    def archived_adjustments(round_id: UUID) -> list[Adjustment]:
        return records(engine.store, engine.store.read_round(str(round_id)).id)

    @app.post("/api/adjustments", dependencies=[Depends(auth.require)])
    def adjust(body: AdjustmentInput) -> Adjustment:
        return apply_action(engine, body, datetime.now(UTC))

    @app.post("/api/adjustments/{action_id}/pay", dependencies=[Depends(auth.require)])
    def pay(action_id: UUID, body: PaymentInput) -> Adjustment:
        return receive_dividend(engine, str(action_id), body, datetime.now(UTC))
