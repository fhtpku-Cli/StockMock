"""0.4.4 截止、延迟与订单原子性：受控报价，真实 SQLite。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random

import pytest

from app import accounting
from app.accounting import FEE, available, execution_amount, money
from app.calendar import calendar, sellable_time
from app.catalog import CATALOG, instrument
from app.engine import Engine
from app.models import DomainError, Instrument, Order
from app.storage import Store
from tests.test_engine import NOW, ControlledProvider, request, sample


def at(hour: int, minute: int) -> datetime:
    return NOW.replace(hour=hour, minute=minute)


def test_lunch_when_last_candidate_processed(engine: Engine) -> None:
    # Given: 上午单最后候选 11:30 已处理，随后午休撤单。
    order = engine.place(request(), NOW)
    q = replace(sample(engine, order, "101"), market_time=at(3, 30), interval_start=at(3, 25))
    engine.process(order.symbol, (q,), at(3, 33))
    _ = engine.cancel(order.id, at(3, 40))
    # When: 下一轮无需新报价。
    engine.process(order.symbol, (), at(3, 41))
    # Then: 正常撤单且无现金流水。
    state = engine.store.read()
    assert state.orders[0].status == "cancelled"
    assert "全部可成交区间已处理" in state.orders[0].status_reason
    assert available(state, "CNY") == 1000000
    assert not state.ledger


def test_lunch_when_no_candidate(engine: Engine) -> None:
    # Given: 午休挂出又撤单，没有完整交易区间。
    # 下单新鲜度不变；受控午休即时估值不作为成交样本。
    engine.provider = ControlledProvider(quote_time=at(4, 0))
    order = engine.place(request(), at(4, 0))
    _ = engine.cancel(order.id, at(4, 10))
    # When: 撤单后处理。
    engine.process(order.symbol, (), at(4, 11))
    # Then: 没有候选区间时直接撤单。
    state = engine.store.read()
    assert state.orders[0].status == "cancelled"
    assert "没有可成交" in state.orders[0].status_reason
    assert available(state, "CNY") == 1000000
    assert not state.ledger


def test_lunch_when_last_candidate_missing(engine: Engine) -> None:
    # Given: 上午挂单，11:40 撤单，最后区间缺失。
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, at(3, 40))
    # When: 11:50 后仍无样本。
    engine.process(order.symbol, (), at(3, 51))
    # Then: 按最后候选 +20 分钟终止，无交易。
    state = engine.store.read()
    assert state.orders[0].status == "terminated"
    assert "数据不足" in state.orders[0].status_reason
    assert available(state, "CNY") == 1000000
    assert not state.ledger


@pytest.mark.parametrize("cancel", [False, True])
def test_delayed_fill_when_market_time_valid(engine: Engine, *, cancel: bool) -> None:
    # Given: 原订单与迟到的有效区间；撤单时刻在市场区间之后。
    order = engine.place(request(), NOW)
    if cancel:
        _ = engine.cancel(order.id, NOW + timedelta(minutes=10))
    q = sample(engine, order, offset=5)
    # When: 服务延迟 47 分钟后取得该区间。
    engine.process(order.symbol, (q,), NOW + timedelta(minutes=47))
    # Then: 成交时间按市场时间，确认时间按实际处理；T+1 与费用准确。
    state = engine.store.read()
    saved = state.orders[0]
    assert saved.status == "filled"
    assert saved.filled_at == NOW + timedelta(minutes=5)
    assert saved.confirmed_at == NOW + timedelta(minutes=47)
    assert "后台延迟处理，按市场时间 10:05" in saved.status_reason
    assert ("撤单未生效" in saved.status_reason) == cancel
    assert saved.fee == Decimal("4.95")
    assert state.cash["CNY"] == Decimal("990095.05")
    assert state.lots[0].sellable_at == sellable_time(instrument(order.symbol), q.market_time)


@pytest.mark.parametrize(("price", "expected"), [("98", "filled"), ("99.50", "expired")])
@pytest.mark.parametrize("early", [False, True])
def test_us_last_bar_when_equal_cutoff(tmp_path: Path, price: str, expected: str, *, early: bool) -> None:
    # Given: 常规或提前收盘日，截止前八分钟受理订单。
    close = datetime(2026, 11, 27, 18, tzinfo=UTC) if early else at(20, 0)
    provider = ControlledProvider(quote_time=close - timedelta(minutes=10))
    engine = Engine(Store(tmp_path / "us.sqlite3"), provider, CATALOG)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(1000)
        engine.store.save(connection, state)
    order = engine.place(
        request().model_copy(update={"symbol": "AAPL", "quantity": 1, "limit_price": Decimal(99)}),
        close - timedelta(minutes=8),
    )
    q = replace(
        provider.quote(order.symbol),
        price=Decimal(price),
        market_time=close,
        interval_start=close - timedelta(minutes=5),
    )
    # When: 最后一根区间在定稿后处理。
    engine.process(order.symbol, (q,), close + timedelta(minutes=2))
    # Then: 截止相等参与成交；未满足则立即到期，无预留残留。
    state = engine.store.read()
    assert state.orders[0].status == expected
    assert available(state, "USD") == state.cash["USD"]
    if expected == "filled":
        assert state.orders[0].filled_at == close
        assert state.cash["USD"] == Decimal("901.95")
        assert state.orders[0].fee == Decimal(".05")
    else:
        assert "全部可成交区间已处理" in state.orders[0].status_reason
        assert state.cash["USD"] == 1000
        assert not state.ledger


@pytest.mark.parametrize("market", ["CN", "US"])
def test_expiry_when_last_bar_missing_or_after_cutoff(engine: Engine, market: str) -> None:
    # Given: 缺失末根区间的挂单。
    if market == "US":
        engine.provider = ControlledProvider(quote_time=at(14, 0))
    order = engine.place(
        request().model_copy(update={"symbol": "AAPL" if market == "US" else "600519.SS"}),
        at(14, 0) if market == "US" else NOW,
    )
    # When: 超过最后区间二十分钟。
    engine.process(order.symbol, (), order.expires_at + timedelta(minutes=21))
    # Then: 数据不足，之后即使补报也不回填。
    state = engine.store.read()
    assert state.orders[0].status == "terminated"
    engine.process(order.symbol, (sample(engine, order, offset=5),), order.expires_at + timedelta(minutes=22))
    assert engine.store.read() == state


@pytest.mark.parametrize("symbol", ["600519.SS", "AAPL"])
def test_after_cutoff_bar_never_fills(engine: Engine, symbol: str) -> None:
    # Given: A 股 14:57 或美股 16:00 截止。
    now = NOW if symbol == "600519.SS" else at(14, 0)
    engine.provider = ControlledProvider(quote_time=now)
    order = engine.place(request().model_copy(update={"symbol": symbol}), now)
    event = order.expires_at + timedelta(minutes=3 if symbol == "600519.SS" else 5)
    q = replace(sample(engine, order), market_time=event, interval_start=event - timedelta(minutes=5))
    # When: 收到截止后的区间。
    engine.process(symbol, (q,), event + timedelta(minutes=2))
    # Then: 正常到期，无现金和持仓变化。
    state = engine.store.read()
    assert state.orders[0].status == "expired"
    assert not state.lots
    assert not state.ledger


def test_cn_expiry_when_final_candidate_processed(engine: Engine) -> None:
    # Given: 14:55 区间未满足限价。
    order = engine.place(request(), NOW)
    q = replace(sample(engine, order, "101"), market_time=at(6, 55), interval_start=at(6, 50))
    engine.process(order.symbol, (q,), at(6, 56))
    # When: 到达 14:57，不需要 15:00 样本。
    engine.process(order.symbol, (), at(6, 57))
    # Then: 立即到期。
    assert engine.store.read().orders[0].status == "expired"
    assert not engine.store.read().ledger


def test_reservation_when_decimal_rounding(tmp_path: Path) -> None:
    # Given: 覆盖低于一美元四位小数及普通两位小数的确定性随机输入。
    random = Random(4407)  # noqa: S311 - 确定性取整抽样，不用于安全用途
    mismatches = 0
    # When: 20 万组使用产品成交算法与预留公式对照。
    for index in range(200000):
        price = Decimal(random.randint(1, 9999 if index % 2 else 100000)) / (10000 if index % 2 else 100)
        quantity = random.randint(1, 100000)
        gross, fee = execution_amount(price, quantity)
        reserve = max(money(price * quantity * (1 + FEE), reserve=True), gross + fee)
        mismatches += reserve < gross + fee
    # Then: 无少预留；具体旧失败输入从真实 place 到 process 成交。
    assert mismatches == 0
    engine = Engine(
        Store(tmp_path / "rounding.sqlite3"), ControlledProvider(quote_time=at(14, 0), price=Decimal(".7825"))
    )
    order = engine.place(
        request().model_copy(update={"symbol": "AAPL", "quantity": 23246, "limit_price": Decimal(".7825")}), at(14, 0)
    )
    assert order.reserved_cny == Decimal("127393.70")
    engine.process(order.symbol, (sample(engine, order, ".7825", 5),), at(14, 6))
    assert engine.store.read().orders[0].status == "filled"


@pytest.mark.parametrize("failure", ["cash", "shares", "calendar"])
def test_failed_order_is_atomic_and_next_fills(engine: Engine, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    # Given: 同证券两单，首单分别定向触发资金、持仓或原晚期日历校验失败。
    first = engine.place(request(), NOW)
    second = engine.place(request(), NOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        if failure == "cash":
            state.orders[0] = first.model_copy(update={"reserved_cny": Decimal(0)})
        elif failure == "shares":
            state.orders[0] = first.model_copy(update={"side": "sell", "price_bound": Decimal(99)})
        engine.store.save(connection, state)
    if failure == "calendar":
        original = sellable_time
        calls = 0

        def failing_once(item: Instrument, event: datetime) -> datetime:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise DomainError("calendar_unavailable", "受控日历失败")
            return original(item, event)

        monkeypatch.setattr(accounting, "sellable_time", failing_once)
    # When: 同一 process 处理两单。
    engine.process(first.symbol, (sample(engine, first, offset=5),), NOW + timedelta(minutes=6))
    # Then: 失败单无现金/持仓/流水残留，后一单成交且占用释放。
    state = engine.store.read()
    assert [o.status for o in state.orders] == ["terminated", "filled"]
    assert "成交校验未通过" in state.orders[0].status_reason
    assert state.cash["CNY"] == Decimal("990095.05")
    assert len(state.lots) == 1
    assert state.lots[0].quantity == 100
    assert len(state.ledger) == 2
    assert {entry.order_id for entry in state.ledger} == {second.id}
    assert available(state, "CNY") == state.cash["CNY"]


def test_real_calendar_end_is_atomic(engine: Engine) -> None:
    # Given: 真实交易日历最后一天的买单及同证券较早截止单。
    first = engine.place(request(), NOW)
    second = engine.place(request(), NOW)
    last = calendar("CN").last_session.to_pydatetime().replace(hour=2, tzinfo=UTC)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders[:] = [
            first.model_copy(update={"created_at": last, "active_at": last, "expires_at": last + timedelta(hours=4)}),
            second.model_copy(
                update={"created_at": last, "active_at": last, "expires_at": last + timedelta(minutes=1)}
            ),
        ]
        engine.store.save(connection, state)
    q = replace(sample(engine, first), market_time=last + timedelta(minutes=5), interval_start=last)
    # When: 实际 sellable_time 无下一交易日。
    engine.process(first.symbol, (q,), last + timedelta(minutes=6))
    # Then: 该单终止，后单正常到期，账户无任何部分交易。
    state = engine.store.read()
    assert [o.status for o in state.orders] == ["terminated", "expired"]
    assert "calendar_unavailable" in state.orders[0].status_reason
    assert state.cash["CNY"] == 1000000
    assert not state.lots
    assert not state.ledger


def test_four_concurrent_requests_and_samples_are_once(engine: Engine) -> None:
    # Given: 同请求和同完整样本从四个线程进入真实 SQLite 事务。
    body = request()

    def place(_index: int) -> Order:
        return engine.place(body, NOW)

    def process(_index: int) -> None:
        engine.process(body.symbol, (quote,), NOW + timedelta(minutes=6))

    # When: 四次提交随后四次撮合同一事件。
    with ThreadPoolExecutor(max_workers=4) as pool:
        orders = list(pool.map(place, range(4)))
        quote = sample(engine, orders[0], offset=5)
        _ = list(pool.map(process, range(4)))
    # Then: 唯一受理、唯一成交和唯一两条记账。
    state = engine.store.read()
    assert len({order.id for order in orders}) == 1
    assert len(state.orders) == 1
    assert state.orders[0].status == "filled"
    assert len(state.ledger) == 2
    assert state.cash["CNY"] == Decimal("990095.05")


def test_preopen_cancel_is_immediate(engine: Engine) -> None:
    # Given: 开盘前受理的限价预挂。
    now = at(1, 0)
    engine.provider = ControlledProvider(quote_time=now)
    order = engine.place(request(), now)
    # When: 开盘前撤单。
    saved = engine.cancel(order.id, now + timedelta(minutes=5))
    # Then: 立即撤销，无候选等待和占用。
    assert saved.status == "cancelled"
    state = engine.store.read()
    assert available(state, "CNY") == 1000000
    assert not state.ledger
