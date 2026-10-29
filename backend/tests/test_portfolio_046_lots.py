"""批次资格边界与真实交易行为，所有数据均为受控隔离样本。"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import accounting
from app.adjustments import apply_action
from app.engine import Engine
from app.models import DomainError, Instrument
from tests.portfolio_046_support import QNOW, T1, T2, mixed, position
from tests.test_analysis import evidence
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_personal import action


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (QNOW, (200, 200, T1, 100)),
        (T1 - timedelta(microseconds=1), (200, 200, T1, 100)),
        (T1, (300, 100, T2, 100)),
        (T1 + timedelta(microseconds=1), (300, 100, T2, 100)),
        (T2, (400, 0, None, 0)),
        (T2 + timedelta(microseconds=1), (400, 0, None, 0)),
    ],
)
def test_remaining_lots_merge_offsets_and_unlock_at_exact_instant(
    engine: Engine, now: datetime, expected: tuple[int, int, datetime | None, int]
) -> None:
    # Given: 标准500股，60+40两Lot表示同一绝对时刻，实际卖单占100。
    _ = mixed(engine)
    before = engine.store.read().model_dump_json()
    # When: 使用同一服务端时刻读取资格与摘要。
    result = position(engine, now)
    # Then: 等号即解锁；只推进最早批次，不把后续批次提前。
    assert result.sellability is not None
    assert result.quantity == 500
    assert result.reserved == 100
    assert (
        result.sellable,
        result.sellability.locked_quantity,
        result.sellability.next_unlock_at,
        result.sellability.next_unlock_quantity,
    ) == expected
    assert engine.store.read().model_dump_json() == before
    evidence(f"q03-boundary-{now.isoformat()}.json", result.model_dump_json())


def test_microsecond_unlock_across_beijing_midnight_never_recalculates_calendar(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 已保存的旧批次在北京时间零点后123456微秒解锁。
    sell = mixed(engine)
    _ = engine.cancel(sell.id, QNOW)
    unlock = datetime.fromisoformat("2026-10-30T00:00:00.123456+08:00")
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots[:] = [state.lots[0].model_copy(update={"sellable_at": unlock})]
        engine.store.save(connection, state)

    def forbidden(_item: Instrument, _event: datetime) -> datetime:
        pytest.fail("读取旧Lot不得重新调用sellable_time")

    monkeypatch.setattr(accounting, "sellable_time", forbidden)
    # When: 跨北京午夜并依次读取微秒边界。
    for now in (
        unlock - timedelta(seconds=1),
        unlock - timedelta(microseconds=1),
        unlock,
        unlock + timedelta(microseconds=1),
    ):
        result = position(engine, now)
        # Then: 显示时刻保留微秒，资格仅由完整时刻比较得到。
        assert result.sellability is not None
        assert result.sellable == (300 if now >= unlock else 0)
        assert result.sellability.next_unlock_at == (None if now >= unlock else unlock)
        evidence(f"q03-midnight-{now.isoformat()}.json", result.model_dump_json())


@pytest.mark.parametrize("mode", ["locked", "reserved", "available"])
def test_pure_lock_pure_reservation_and_all_available(engine: Engine, mode: str) -> None:
    # Given: 标准样本裁剪为单一限制，撤单使用真实Engine。
    sell = mixed(engine)
    if mode != "reserved":
        _ = engine.cancel(sell.id, QNOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots[:] = [lot for lot in state.lots if (lot.sellable_at > QNOW) == (mode == "locked")]
        engine.store.save(connection, state)
    # When: 读取各类限制。
    result = position(engine, QNOW)
    # Then: 锁定与占用互不冒充，没有锁定时严格0/null/0。
    assert result.sellability is not None
    assert result.reserved == (100 if mode == "reserved" else 0)
    assert result.sellability.locked_quantity == (200 if mode == "locked" else 0)
    if mode != "locked":
        assert result.sellability.next_unlock_at is None
        assert result.sellability.next_unlock_quantity == 0
    evidence(f"q03-pure-{mode}.json", result.model_dump_json())


@pytest.mark.parametrize(("now", "available"), [(QNOW, 200), (T1, 300), (T2, 400)])
def test_real_engine_preview_agrees_with_projected_sellability(engine: Engine, now: datetime, available: int) -> None:
    # Given: 真实卖单预留100，合法申报步长100；有效受控报价和前一交易日日线。
    _ = mixed(engine)
    prior = {QNOW: "2026-10-28", T1: "2026-10-29", T2: "2026-10-30"}[now]
    engine.provider = ControlledProvider(quote_time=now, history_day=datetime.fromisoformat(prior).date())
    engine.clock = lambda: now
    body = request().model_copy(update={"side": "sell", "quantity": available})
    before = engine.store.read()
    # When: 实际Engine预览合法数量及多100股的非法数量。
    accepted = engine.preview(body, now)
    with pytest.raises(DomainError) as failure:
        _ = engine.preview(body.model_copy(update={"quantity": available + 100}), now)
    # Then: 真实交易资格和只读解释一致，预览不写账。
    assert failure.value.code == "insufficient_shares"
    assert position(engine, now).sellable == available
    assert engine.store.read() == before
    evidence(f"q03-preview-{now.date()}.json", accepted.model_dump_json())
    evidence(f"q03-preview-rejected-{now.date()}.txt", failure.value.code)


@pytest.mark.parametrize("fills", [True, False])
def test_cancel_pending_then_actual_terminal_updates_reservation(engine: Engine, *, fills: bool) -> None:
    # Given: 标准样本真实卖单，申请撤单不会释放占用。
    sell = mixed(engine)
    cutoff = QNOW + timedelta(minutes=6 if fills else 1)
    pending = engine.cancel(sell.id, cutoff)
    assert pending.status == "cancel_pending"
    before = position(engine, cutoff)
    assert before.reserved == 100
    evidence(f"q03-pending-before-{fills}.json", before.model_dump_json())
    # When: 有效完整样本由实际Engine撮合，在撤单截止前/后得到不同终态。
    engine.process(sell.symbol, (sample(engine, sell, "100"),), QNOW + timedelta(minutes=7))
    # Then: 成交消耗持仓；撤单才把原占用恢复可卖，锁定批次保持。
    state = engine.store.read()
    result = position(engine, QNOW + timedelta(minutes=7))
    assert state.orders[0].status == ("filled" if fills else "cancelled")
    assert result.quantity == (400 if fills else 500)
    assert result.sellable == (200 if fills else 300)
    assert result.reserved == 0
    assert result.sellability is not None
    assert result.sellability.locked_quantity == 200
    assert result.sellability.next_unlock_quantity == 100
    evidence(f"q03-terminal-{state.orders[0].status}.json", result.model_dump_json())
    evidence(f"q03-terminal-{state.orders[0].status}-state.json", state.model_dump_json())


@pytest.mark.parametrize("scenario", [("600519.SS", NOW, 100), ("AAPL", NOW.replace(hour=14), 0)])
def test_real_buy_t_plus_zero_and_t_plus_one(engine: Engine, scenario: tuple[str, datetime, int]) -> None:
    # Given: 沿用已核验市场规则和受控可成交行情。
    symbol, now, locked = scenario
    engine.provider = ControlledProvider(quote_time=now)
    buy = engine.place(request().model_copy(update={"symbol": symbol}), now)
    # When: 真实Engine成交后读取剩余Lot资格。
    engine.process(symbol, (sample(engine, buy, "100"),), now + timedelta(minutes=6))
    result = position(engine, now + timedelta(minutes=6))
    # Then: 不把全部品种统一解释成T+1。
    assert engine.store.read().orders[0].status == "filled"
    assert result.sellability is not None
    assert result.sellability.locked_quantity == locked
    assert result.sellable == 100 - locked
    evidence(f"q03-real-buy-{symbol}.json", result.model_dump_json())


def test_split_rejects_locked_then_projects_real_integer_split(engine: Engine) -> None:
    # Given: 真实取消占用后仍含未解锁批次。
    sell = mixed(engine)
    _ = engine.cancel(sell.id, QNOW)
    body = action(engine).model_copy(
        update={"kind": "split", "expected_quantity": 500, "cash_per_share": None, "numerator": 2, "denominator": 1}
    )
    before = engine.store.read()
    with pytest.raises(DomainError) as failure:
        _ = apply_action(engine, body, QNOW)
    assert failure.value.code == "unsettled_position"
    assert engine.store.read() == before
    evidence("q03-split-locked-rejected.txt", failure.value.code)
    # When: 全部批次解锁后实际执行合法整数拆股。
    adjustment = apply_action(engine, body.model_copy(update={"effective_date": T2.date()}), T2)
    result = position(engine, T2)
    # Then: 投影使用拆股后的剩余数量及原算法保存的时刻。
    assert adjustment.quantity_after == result.quantity == result.sellable == 1000
    assert result.sellability is not None
    assert result.sellability.locked_quantity == result.sellability.next_unlock_quantity == 0
    assert result.sellability.next_unlock_at is None
    assert sum(lot.cost for lot in engine.store.read().lots) == Decimal(45000)
    evidence("q03-split.json", result.model_dump_json())
    evidence("q03-split-adjustment.json", adjustment.model_dump_json())
