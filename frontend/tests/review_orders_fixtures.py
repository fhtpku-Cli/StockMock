"""E1 受控历史夹具：新建数据库，不读取私人数据，不声称真实撮合。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import uuid4

from app.api_models import ResetInput
from app.engine import Engine
from app.models import Lot, Model, Order, PlanSnapshot
from app.operations import reset
from app.plan_models import Plan, PlanInput, PlanUpdate
from app.plans import create_plan, update_plan

NOW: Final = datetime(2026, 10, 23, 2, tzinfo=UTC)


class Subject(Model):
    id: str
    round_id: str
    orders: list[str]
    subject_type: str = "plan"


class Fixture(Model):
    source: str = "临时SQLite受控历史；非真实市场成交"
    subjects: dict[str, Subject]


def plan(engine: Engine, label: str) -> Plan:
    return create_plan(
        engine,
        PlanInput(
            symbol="510300.SS",
            direction="buy",
            status="draft",
            reason=f"E1受控：{label}",
            target_price=Decimal(10),
            stop_price=Decimal(8),
            review_date=NOW.date(),
        ),
        NOW,
    )


def order_for(owner: Plan, number: int) -> Order:
    created = NOW + timedelta(minutes=number // 3)
    return Order(
        id=str(uuid4()),
        request_id=str(uuid4()),
        plan_id=owner.id,
        plan_version=owner.version,
        plan_snapshot=PlanSnapshot.model_validate(
            owner.model_dump(exclude={"monitoring"})
        ),
        symbol=owner.symbol,
        side="buy",
        quantity=100,
        limit_price=Decimal(10),
        price_bound=Decimal(10),
        currency="CNY",
        source="E1受控历史",
        created_at=created,
        active_at=created + timedelta(seconds=60),
        expires_at=created + timedelta(hours=5),
        status="expired",
        status_reason="受控到期历史",
        confirmed_at=created + timedelta(hours=6),
    )


def subject(owner: Plan, orders: list[Order]) -> Subject:
    return Subject(
        id=owner.id,
        round_id=owner.round_id,
        orders=[
            entry.id
            for entry in sorted(orders, key=lambda entry: (entry.created_at, entry.id))
        ],
    )


def seed(engine: Engine) -> Fixture:
    archived = plan(engine, "B归档未成交")
    b_order = order_for(archived, 0).model_copy(
        update={"cancel_at": NOW + timedelta(hours=4)}
    )
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders.append(b_order)
        engine.store.save(connection, state)
    reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    subjects = {"b": subject(archived, [b_order])}
    a = plan(engine, "A第一版")
    buy = order_for(a, 0).model_copy(
        update={
            "status": "filled",
            "quantity": 200,
            "fill_price": Decimal(10),
            "filled_at": NOW + timedelta(minutes=5),
            "confirmed_at": NOW + timedelta(minutes=6),
            "fee": Decimal("0.50"),
        }
    )
    a = update_plan(
        engine,
        a.id,
        PlanUpdate(
            symbol=a.symbol,
            direction=a.direction,
            status=a.status,
            reason="E1受控：A第二版",
            target_price=Decimal(11),
            stop_price=a.stop_price,
            review_date=a.review_date,
            version=a.version,
        ),
    )
    sell = order_for(a, 3).model_copy(
        update={
            "side": "sell",
            "status": "filled",
            "fill_price": Decimal(12),
            "filled_at": NOW + timedelta(minutes=10),
            "confirmed_at": NOW + timedelta(minutes=11),
            "fee": Decimal("0.85"),
            "realized_pnl": Decimal("198.90"),
            "cost_basis": Decimal("1000.25"),
        }
    )
    orders = [buy, sell]
    subjects["a"] = subject(a, orders)
    for label, count in (
        ("empty", 0),
        ("single", 1),
        ("other", 1),
        ("mixed", 7),
        ("many21", 21),
        ("many508", 508),
    ):
        owner = plan(engine, label)
        entries = [order_for(owner, index) for index in range(count)]
        if label == "mixed":
            statuses = [
                "accepted",
                "cancel_pending",
                "filled",
                "expired",
                "cancelled",
                "terminated",
                "filled",
            ]
            entries = [
                entry.model_copy(update={"status": status})
                for entry, status in zip(entries, statuses, strict=True)
            ]
            entries[1] = entries[1].model_copy(update={"cancel_at": NOW})
            entries[2] = entries[2].model_copy(
                update={
                    "fill_price": Decimal("10.123"),
                    "filled_at": NOW + timedelta(minutes=5),
                }
            )
            entries[6] = entries[6].model_copy(update={"plan_snapshot": None})
        if label == "many508":
            entries = [
                entry.model_copy(
                    update={
                        "status": "filled",
                        "fill_price": Decimal("10.123"),
                        "filled_at": NOW,
                    }
                )
                for entry in entries
            ]
        subjects[label] = subject(owner, entries)
        orders.extend(reversed(entries))
    legacy = order_for(a, 0).model_copy(
        update={"plan_id": None, "plan_version": None, "plan_snapshot": None}
    )
    subjects["legacy"] = Subject(
        id=legacy.id, round_id=a.round_id, orders=[legacy.id], subject_type="order"
    )
    orders.append(legacy)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders.extend(orders)
        state.lots.append(
            Lot(symbol=a.symbol, quantity=100, cost=Decimal("1000.25"), sellable_at=NOW)
        )
        engine.store.save(connection, state)
    return Fixture(subjects=subjects)
