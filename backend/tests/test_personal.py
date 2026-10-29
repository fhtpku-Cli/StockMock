"""公司行动和每日观察使用真实 SQLite，行情仅为隔离测试数据。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.adjustments import apply_action, receive_dividend, records
from app.api_models import ResetInput
from app.engine import Engine
from app.main import create_app
from app.models import DomainError, Lot
from app.operations import reset
from app.personal_models import Adjustment, AdjustmentInput, PaymentInput, ValuationSnapshot
from app.snapshots import capture, snapshots
from app.storage import Store
from app.valuation import value_account
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request, sample


def held(engine: Engine, quantity: int = 100) -> None:
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="600519.SS", quantity=quantity, cost=Decimal(9000), sellable_at=NOW))
        state.cash["CNY"] -= Decimal(9000)
        engine.store.save(connection, state)


def action(engine: Engine) -> AdjustmentInput:
    return AdjustmentInput(
        request_id=str(uuid4()),
        round_id=engine.store.read().id,
        symbol="600519.SS",
        kind="dividend",
        effective_date=NOW.date(),
        expected_quantity=100,
        cash_per_share=Decimal("1.23"),
        note="已核对公告和税前股息金额",
    )


def test_dividend_concurrent_retries_and_semantic_duplicate(engine: Engine) -> None:
    held(engine)
    body = action(engine)

    def apply(_index: int) -> Adjustment:
        return apply_action(engine, body, NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, range(2)))
    assert results[0] == results[1]
    assert engine.store.read().cash["CNY"] == Decimal(991123)
    assert len(engine.store.read().ledger) == 1
    assert len(records(Store(engine.store.path), body.round_id)) == 1
    with pytest.raises(DomainError, match="不能重复"):
        _ = apply_action(engine, body.model_copy(update={"request_id": str(uuid4())}), NOW)


def test_receivable_counts_assets_but_is_not_spendable_and_payment_once(engine: Engine) -> None:
    held(engine)
    before = value_account(engine, engine.store.read(), NOW).total
    body = action(engine).model_copy(update={"payment_status": "receivable"})
    result = apply_action(engine, body, NOW)
    assert engine.store.read().cash["CNY"] == Decimal(991000)
    assert before is not None
    assert value_account(engine, engine.store.read(), NOW).total == before + 123
    payment = PaymentInput(request_id=str(uuid4()), round_id=body.round_id)
    paid = receive_dividend(engine, result.id, payment, NOW)
    assert receive_dividend(engine, result.id, payment, NOW) == paid
    assert engine.store.read().cash["CNY"] == Decimal(991123)
    assert value_account(engine, engine.store.read(), NOW).total == before + 123
    assert len(engine.store.read().ledger) == 2


def test_split_preserves_cost_and_awaits_post_action_price(engine: Engine) -> None:
    held(engine)
    body = action(engine).model_copy(update={"kind": "split", "cash_per_share": None, "numerator": 2, "denominator": 1})
    result = apply_action(engine, body, NOW)
    assert result.quantity_after == 200
    assert result.cost_before == result.cost_after == Decimal(9000)
    assert engine.store.read().cash["CNY"] == Decimal(991000)
    assert value_account(engine, engine.store.read(), NOW).status == "stale"
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=1), price=Decimal(50))
    valuation = value_account(engine, engine.store.read(), NOW + timedelta(minutes=1))
    assert valuation.total == Decimal(1001000)


def test_payment_between_state_and_valuation_does_not_mix_balances(engine: Engine) -> None:
    held(engine)
    body = action(engine).model_copy(update={"payment_status": "receivable"})
    result = apply_action(engine, body, NOW)
    old = engine.store.read()
    before = value_account(engine, old, NOW)
    payment = PaymentInput(request_id=str(uuid4()), round_id=body.round_id)
    _ = receive_dividend(engine, result.id, payment, NOW)
    mixed = value_account(engine, old, NOW)
    assert mixed.total is None
    assert mixed.status == "missing"
    assert value_account(engine, engine.store.read(), NOW).total == before.total


def test_split_then_partial_sale_preserves_basis_and_realized_profit(engine: Engine) -> None:
    held(engine)
    body = action(engine).model_copy(update={"kind": "split", "cash_per_share": None, "numerator": 2, "denominator": 1})
    _ = apply_action(engine, body, NOW)
    later = NOW + timedelta(days=1)
    engine.provider = ControlledProvider(quote_time=later, price=Decimal(50), history_day=NOW.date())
    sell = engine.place(request().model_copy(update={"side": "sell", "limit_price": Decimal(50)}), later)
    engine.process(sell.symbol, (sample(engine, sell, "55"),), later + timedelta(minutes=6))
    state = Store(engine.store.path).read()
    assert state.orders[-1].cost_basis == Decimal(4500)
    assert state.orders[-1].realized_pnl == Decimal("997.25")
    assert sum(lot.cost for lot in state.lots) == Decimal(4500)
    assert sum(lot.quantity for lot in state.lots) == 100


def test_fractional_split_and_pending_orders_rollback(engine: Engine) -> None:
    held(engine)
    body = action(engine).model_copy(update={"kind": "split", "cash_per_share": None, "numerator": 1, "denominator": 3})
    before = engine.store.read()
    with pytest.raises(DomainError, match="碎股"):
        _ = apply_action(engine, body, NOW)
    assert engine.store.read() == before
    _ = engine.place(request(), NOW)
    with pytest.raises(DomainError, match="全部挂单"):
        _ = apply_action(engine, action(engine), NOW)
    assert records(engine.store, before.id) == []


def test_snapshot_recovers_same_day_and_preserves_offline_gaps(engine: Engine) -> None:
    held(engine)
    engine.provider = ControlledProvider(quote_time=NOW - timedelta(hours=2))
    capture(engine, NOW)
    points = snapshots(Store(engine.store.path), engine.store.read().id)
    assert len(points) == 1
    assert points[0].status == "stale"
    assert points[0].total_assets_cny is None
    engine.provider = ControlledProvider(quote_time=NOW)
    capture(engine, NOW + timedelta(minutes=1))
    recovered = snapshots(engine.store, engine.store.read().id)[0]
    assert recovered.id == points[0].id
    assert recovered.status == "complete"
    assert recovered.first_failure is not None
    assert recovered.first_failure.captured_at == points[0].captured_at
    assert recovered.first_failure.issues == points[0].issues
    capture(engine, NOW + timedelta(days=3))
    assert len(snapshots(engine.store, engine.store.read().id)) == 2


def test_archived_adjustments_and_snapshots_read_only(engine: Engine) -> None:
    held(engine)
    body = action(engine)
    _ = apply_action(engine, body, NOW)
    capture(engine, NOW)
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        response = client.get(f"/api/rounds/{body.round_id}/snapshots")
        assert response.status_code == 200
        assert ValuationSnapshot.model_validate(response.json()[0]).total_assets_cny is not None
        archived = client.get(f"/api/rounds/{body.round_id}/adjustments")
        assert len(TypeAdapter(list[Adjustment]).validate_json(archived.content)) == 1
        assert client.get("/api/adjustments").json() == []
        retry = body.model_copy(update={"request_id": str(uuid4())})
        assert (
            client.post(
                "/api/adjustments", content=retry.model_dump_json(), headers={"Content-Type": "application/json"}
            ).status_code
            == 409
        )
