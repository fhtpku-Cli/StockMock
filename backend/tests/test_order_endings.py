"""最近结束委托的纯函数与经认证 HTTP 契约测试。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api_models import ResetInput
from app.catalog import CATALOG
from app.engine import Engine
from app.main import create_app
from app.models import Currency, Entry, Order, State
from app.operations import reset
from app.order_endings import EndedStatus, RecentEndings, recent_endings
from tests.test_api import PASSWORD
from tests.test_engine import NOW

OrderStatus = Literal["accepted", "cancel_pending", "filled", "cancelled", "expired", "terminated"]


def make_order(  # noqa: PLR0913 — 测试工厂集中声明委托的所有相关边界。
    identifier: str,
    *,
    status: OrderStatus = "cancelled",
    side: Literal["buy", "sell"] = "buy",
    symbol: str = "600519.SS",
    currency: Currency = "CNY",
    quantity: int = 100,
    confirmed_at: datetime | None = NOW,
    cancel_at: datetime | None = None,
    fill_price: Decimal | None = None,
    filled_at: datetime | None = None,
    reserved_cny: Decimal = Decimal(0),
    reserved_foreign: Decimal = Decimal(0),
) -> Order:
    """构造完全受控的委托，不调用交易或行情路径。"""
    return Order(
        id=identifier,
        request_id=f"request-{identifier}",
        symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=Decimal(10),
        price_bound=Decimal(10),
        status=status,
        status_reason=f"{identifier} 原始状态说明",
        created_at=NOW - timedelta(days=1),
        active_at=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=1),
        confirmed_at=confirmed_at,
        cancel_at=cancel_at,
        fill_price=fill_price,
        filled_at=filled_at,
        currency=currency,
        reserved_cny=reserved_cny,
        reserved_foreign=reserved_foreign,
        source="controlled-test",
    )


def make_state(*orders: Order, ledger: list[Entry] | None = None) -> State:
    """每个纯函数用例使用独立轮次。"""
    return State(
        id="controlled-round",
        created_at=NOW - timedelta(days=5),
        orders=list(orders),
        ledger=ledger if ledger is not None else [],
    )


def make_entry(
    identifier: str,
    currency: Currency,
    amount: str,
    *,
    order_id: str | None = "filled-usd",
    kind: str = "buy",
) -> Entry:
    return Entry(
        id=identifier,
        kind=kind,
        currency=currency,
        amount=Decimal(amount),
        created_at=NOW,
        order_id=order_id,
        note="受控流水",
    )


def test_beijing_window_includes_both_boundaries() -> None:
    """北京今天往前两天的零点与当前时刻都包含，前后一秒排除。"""
    now = datetime(2026, 11, 24, 4, tzinfo=UTC)
    start = datetime(2026, 11, 21, 16, tzinfo=UTC)
    result = recent_endings(
        make_state(
            make_order("before", confirmed_at=start - timedelta(seconds=1)),
            make_order("start", confirmed_at=start),
            make_order("end", confirmed_at=now),
            make_order("after", confirmed_at=now + timedelta(seconds=1)),
        ),
        CATALOG,
        now,
    )

    assert result.window_start == start
    assert result.window_start.isoformat() == "2026-11-22T00:00:00+08:00"
    assert result.window_end == now
    assert [item.order_id for item in result.items] == ["end", "start"]


@pytest.mark.parametrize(("count", "expected_more"), [(10, False), (11, True)])
def test_limit_more_and_stable_ordering(count: int, *, expected_more: bool) -> None:
    """十条本身不算更多；同刻委托按编号升序。"""
    endings = [(NOW - timedelta(minutes=index // 2), f"order-{index:02d}") for index in range(count)]
    orders = [make_order(identifier, confirmed_at=end) for end, identifier in endings]
    endings.sort(key=lambda item: item[1])
    endings.sort(key=lambda item: item[0], reverse=True)

    result = recent_endings(make_state(*orders), CATALOG, NOW)

    assert len(result.items) == 10
    assert [item.order_id for item in result.items] == [identifier for _, identifier in endings[:10]]
    assert result.more is expected_more


def test_pre_activation_cancel_uses_cancel_at_and_missing_times_are_hidden() -> None:
    """生效前撤销没有确认时间也能显示，两个时间都缺失则不推算。"""
    pre_open = make_order("pre-open", confirmed_at=None, cancel_at=NOW - timedelta(minutes=1))
    confirmed = make_order(
        "confirmed-first",
        confirmed_at=NOW - timedelta(minutes=2),
        cancel_at=NOW - timedelta(minutes=1),
    )
    missing = make_order("missing", confirmed_at=None, cancel_at=None)

    result = recent_endings(make_state(pre_open, missing, confirmed), (), NOW)

    assert [item.order_id for item in result.items] == ["pre-open", "confirmed-first"]
    assert result.items[0].ended_at == pre_open.cancel_at
    assert result.items[0].name is None
    assert result.items[1].ended_at == confirmed.confirmed_at


def test_four_ended_statuses_are_included_and_active_statuses_are_excluded() -> None:
    """状态说明原样返回，受理中与撤单待确认即使在窗口也不显示。"""
    ended: tuple[EndedStatus, ...] = ("filled", "cancelled", "expired", "terminated")
    orders = [make_order(status, status=status) for status in ended]
    orders.extend(
        (
            make_order("accepted", status="accepted"),
            make_order("cancel-pending", status="cancel_pending"),
        )
    )

    result = recent_endings(make_state(*orders), CATALOG, NOW)

    assert {item.status for item in result.items} == set(ended)
    assert len(result.items) == 4
    assert all(item.status_reason == f"{item.order_id} 原始状态说明" for item in result.items)


def test_cash_changes_sum_order_ledger_and_fx_without_rounding() -> None:
    """只汇总本委托流水，按币种排序并省略净额零，手工换汇不混入。"""
    order = make_order(
        "filled-usd",
        status="filled",
        symbol="AAPL",
        currency="USD",
        fill_price=Decimal("80.123456789"),
        filled_at=NOW - timedelta(minutes=1),
    )
    ledger = [
        make_entry("fx-out", "CNY", "-700.123456789012345678", kind="fx_out"),
        make_entry("fx-in", "USD", "100.01", kind="fx_in"),
        make_entry("buy", "USD", "-80"),
        make_entry("fee", "USD", "-0.01", kind="fee"),
        make_entry("hkd-in", "HKD", "5", kind="fx_in"),
        make_entry("hkd-out", "HKD", "-5", kind="fx_out"),
        make_entry("other-order", "CNY", "-999", order_id="another-order"),
        make_entry("manual-fx", "USD", "999", order_id=None, kind="fx_in"),
    ]

    result = recent_endings(make_state(order, ledger=ledger), CATALOG, NOW)

    item = result.items[0]
    assert [(change.currency, change.amount) for change in item.cash_changes] == [
        ("CNY", Decimal("-700.123456789012345678")),
        ("USD", Decimal("20.00")),
    ]
    assert item.fill_price == Decimal("80.123456789")
    assert item.filled_at == NOW - timedelta(minutes=1)
    assert item.ended_at == NOW


def test_unfilled_buy_releases_cash_and_sell_releases_shares() -> None:
    """未成交买单返回原预留资金，未成交卖单返回数量，成交单不返回释放额。"""
    buy = make_order(
        "buy",
        status="cancelled",
        currency="USD",
        symbol="AAPL",
        reserved_cny=Decimal("100.123456789"),
        reserved_foreign=Decimal("20.987654321"),
    )
    sell = make_order("sell", status="expired", side="sell", quantity=77)
    filled = make_order("filled", status="filled", reserved_cny=Decimal(100))

    result = recent_endings(make_state(buy, sell, filled), CATALOG, NOW)
    items = {item.order_id: item for item in result.items}

    assert items["buy"].released_cny == Decimal("100.123456789")
    assert items["buy"].released_foreign == Decimal("20.987654321")
    assert items["buy"].released_shares is None
    assert items["sell"].released_cny is None
    assert items["sell"].released_foreign is None
    assert items["sell"].released_shares == 77
    assert items["filled"].released_cny is None
    assert items["filled"].released_foreign is None
    assert items["filled"].released_shares is None


def test_authenticated_route_is_read_only_and_isolated_to_new_round(engine: Engine, api: TestClient) -> None:
    """真实路由验证鉴权、字段类型、目录名称、当前轮次与只读性。"""
    order = make_order(
        "route-filled",
        status="filled",
        symbol="AAPL",
        currency="USD",
        fill_price=Decimal("80.123456789"),
        filled_at=NOW - timedelta(minutes=1),
    )
    ledger = [make_entry("route-buy", "USD", "-80.123456789", order_id=order.id)]
    with engine.store.transaction() as connection:
        engine.store.save(
            connection, engine.store.load(connection).model_copy(update={"orders": [order], "ledger": ledger})
        )
    before = engine.store.read()
    old_round_id = before.id
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as anonymous:
        assert anonymous.get("/api/orders/recent-endings").status_code == 401

    response = api.get("/api/orders/recent-endings")

    assert response.status_code == 200
    result = RecentEndings.model_validate_json(response.content)
    assert result.window_start == datetime(2026, 10, 19, 16, tzinfo=UTC)
    assert result.window_end == NOW
    assert not result.more
    assert len(result.items) == 1
    item = result.items[0]
    assert item.order_id == order.id
    assert item.symbol == "AAPL"
    assert item.name == next(instrument.name for instrument in engine.catalog if instrument.symbol == "AAPL")
    assert item.side == "buy"
    assert item.quantity == 100
    assert item.status == "filled"
    assert item.status_reason == "route-filled 原始状态说明"
    assert item.currency == "USD"
    assert item.fill_price == Decimal("80.123456789")
    assert item.filled_at == NOW - timedelta(minutes=1)
    assert item.ended_at == NOW
    assert [(change.currency, change.amount) for change in item.cash_changes] == [("USD", Decimal("-80.123456789"))]
    assert item.released_cny is None
    assert item.released_foreign is None
    assert item.released_shares is None
    assert engine.store.read() == before

    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    isolated = RecentEndings.model_validate_json(api.get("/api/orders/recent-endings").content)
    assert isolated.items == []
    assert not isolated.more
    assert engine.store.read().id != old_round_id
