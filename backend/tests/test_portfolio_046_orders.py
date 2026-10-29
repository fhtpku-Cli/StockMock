"""活动委托真实状态及估值期间一致性。"""

from datetime import timedelta
from decimal import Decimal
from typing import override
from uuid import uuid4

import pytest

from app.api_models import ResetInput
from app.engine import Engine
from app.marketdata import Quote
from app.models import ActiveOrdersView, DomainError, Order, WorkerView
from app.operations import reset
from app.views import portfolio
from tests.portfolio_046_support import QNOW, mixed
from tests.test_analysis import evidence
from tests.test_engine import ControlledProvider, request
from tests.test_snapshots import UnavailableProvider


def test_counts_empty_and_one_real_order(engine: Engine) -> None:
    # Given: 全新SQLite与受控行情。
    empty = portfolio(engine, QNOW, WorkerView())
    assert empty.active_orders == ActiveOrdersView(accepted_count=0, cancel_pending_count=0)
    _ = mixed(engine)
    # When: 实际卖单受理后的资产读取。
    result = portfolio(engine, QNOW, WorkerView())
    # Then: 单笔真实状态进入摘要。
    assert result.active_orders == ActiveOrdersView(accepted_count=1, cancel_pending_count=0)
    evidence("q02-empty.json", empty.model_dump_json())
    evidence("q02-one.json", result.model_dump_json())


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("status", ["accepted", "cancel_pending", "filled", "cancelled", "expired", "terminated"])
def test_counts_use_status_even_before_active_or_after_cutoff(engine: Engine, side: str, status: str) -> None:
    # Given: 实际受理订单作为结构样本，控制状态、方向和截止时刻。
    order = mixed(engine)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        payload = order.model_dump()
        payload.update({"side": side, "status": status, "expires_at": QNOW - timedelta(seconds=1)})
        state.orders[:] = [Order.model_validate(payload)]
        engine.store.save(connection, state)
    # When: 截止已过但未推断终态的读取。
    result = portfolio(engine, QNOW, WorkerView())
    # Then: 仅真实accepted/cancel_pending计数，所有终态排除。
    assert result.active_orders == ActiveOrdersView(
        accepted_count=int(status == "accepted"), cancel_pending_count=int(status == "cancel_pending")
    )
    evidence(f"q02-{side}-{status}-past-cutoff.json", result.model_dump_json())


def test_fixed_count_with_future_order_and_all_terminal_states(engine: Engine) -> None:
    # Given: 真实卖单、预挂买单、过截止待撤买单，混入全部终态。
    sell = mixed(engine)
    buy = engine.place(request(), QNOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        future = buy.model_copy(update={"active_at": QNOW + timedelta(days=1)})
        pending = buy.model_copy(
            update={"id": str(uuid4()), "status": "cancel_pending", "expires_at": QNOW - timedelta(seconds=1)}
        )
        state.orders[:] = [sell, future, pending]
        for status in ("filled", "cancelled", "expired", "terminated"):
            state.orders.append(buy.model_copy(update={"id": str(uuid4()), "status": status}))
        engine.store.save(connection, state)
    # When: 缺行情也读取真实账本数量。
    engine.provider = UnavailableProvider()
    result = portfolio(engine, QNOW, WorkerView())
    # Then: 固定集合2+1，持仓资格不随估值缺失而消失。
    assert result.active_orders == ActiveOrdersView(accepted_count=2, cancel_pending_count=1)
    assert result.total_assets_cny is None
    assert result.positions[0].sellable == 200
    assert result.positions[0].sellability is not None
    assert result.positions[0].sellability.locked_quantity == 200
    evidence("q02-fixed-missing-market.json", result.model_dump_json())


def test_order_mutation_during_valuation_reloads_all_projections(engine: Engine) -> None:
    # Given: 报价调用中由真实cancel把卖单转为待确认。
    sell = mixed(engine)

    class CancellingProvider(ControlledProvider):
        @override
        def quote(self, symbol: str) -> Quote:
            _ = engine.cancel(sell.id, QNOW + timedelta(seconds=1))
            return super().quote(symbol)

    engine.provider = CancellingProvider(quote_time=QNOW)
    # When: portfolio初次估值遇到委托变化。
    result = portfolio(engine, QNOW, WorkerView())
    # Then: 计数、占用与持仓来自重读后的同一State。
    assert result.active_orders == ActiveOrdersView(accepted_count=0, cancel_pending_count=1)
    assert result.positions[0].reserved == 100
    assert result.positions[0].sellable == 200
    assert result.total_assets_cny == 1050000
    evidence("q02-order-change.json", result.model_dump_json())


def test_reset_during_valuation_returns_only_new_round(engine: Engine) -> None:
    # Given: 旧轮有持仓，卖单在生效等号真实取消，报价时实际reset。
    sell = mixed(engine)
    _ = engine.cancel(sell.id, QNOW)
    old = engine.store.read()
    calls: list[str] = []

    class ResettingProvider(ControlledProvider):
        @override
        def quote(self, symbol: str) -> Quote:
            fresh = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), QNOW)
            calls.append(fresh.round_id)
            return super().quote(symbol)

    engine.provider = ResettingProvider(quote_time=QNOW)
    # When: 真实换轮发生在估值中。
    result = portfolio(engine, QNOW, WorkerView())
    # Then: 旧持仓、旧摘要和旧现金没有拼入新轮。
    assert result.round_id == calls[0] != old.id
    assert len(calls) == 1
    assert result.positions == []
    assert result.active_orders == ActiveOrdersView(accepted_count=0, cancel_pending_count=0)
    assert result.cash[0].balance == result.total_assets_cny == Decimal(1000000)
    evidence("q02-reset.json", result.model_dump_json())


def test_continuous_order_mutation_keeps_409(engine: Engine) -> None:
    # Given: 每次报价均有真实数据库订单更新，无法得到一致轮次。
    _ = mixed(engine)
    calls: list[str] = []

    class ChangingProvider(ControlledProvider):
        @override
        def quote(self, symbol: str) -> Quote:
            with engine.store.transaction() as connection:
                state = engine.store.load(connection)
                state.orders.append(state.orders[0].model_copy(update={"id": str(uuid4()), "side": "buy"}))
                engine.store.save(connection, state)
                calls.append(state.id)
            return super().quote(symbol)

    engine.provider = ChangingProvider(quote_time=QNOW)
    # When: 有界重读仍持续变化。
    with pytest.raises(DomainError) as failure:
        _ = portfolio(engine, QNOW, WorkerView())
    # Then: 保留409而不发布混合摘要。
    assert failure.value.code == "valuation_changed"
    assert failure.value.status == 409
    assert len(calls) == 3
    evidence(
        "q02-continuous-change.txt", f"HTTP {failure.value.status}\n{failure.value.code}\nquote_calls={len(calls)}"
    )
