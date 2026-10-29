"""0.4.6 受控时间与真实 SQLite 的共享样本。"""

from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from typing import ClassVar, Final

from app.engine import Engine
from app.models import Lot, Order, PositionView, WorkerView
from app.views import portfolio
from tests.test_engine import ControlledProvider, request

QNOW: Final = datetime(2026, 10, 29, 2, tzinfo=UTC)
T1: Final = datetime(2026, 10, 30, 1, 30, tzinfo=UTC)
T2: Final = datetime(2026, 11, 2, 1, 30, tzinfo=UTC)


class Clock:
    current: ClassVar[datetime] = QNOW

    @classmethod
    def now(cls, _tz: tzinfo | None = None) -> datetime:
        return cls.current


def mixed(engine: Engine) -> Order:
    """标准500股样本，100股由真实卖单占用。"""
    engine.provider = ControlledProvider(quote_time=QNOW, history_day=QNOW.date().replace(day=28))
    engine.clock = lambda: QNOW
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.extend(
            Lot(symbol="600519.SS", quantity=quantity, cost=Decimal(quantity) * 90, sellable_at=unlock)
            for quantity, unlock in (
                (300, QNOW),
                (60, T1),
                (40, datetime.fromisoformat("2026-10-30T09:30:00+08:00")),
                (100, T2),
            )
        )
        engine.store.save(connection, state)
    return engine.place(request().model_copy(update={"side": "sell"}), QNOW)


def position(engine: Engine, now: datetime) -> PositionView:
    """读取真实资产投影并核对总量分解。"""
    result = portfolio(engine, now, WorkerView()).positions[0]
    assert result.sellability is not None
    assert result.quantity == result.sellable + result.reserved + result.sellability.locked_quantity
    return result
