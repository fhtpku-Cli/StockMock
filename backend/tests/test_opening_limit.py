"""R01：隔离SQLite与受控行情验证开段限价受理边界。"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import override

import pytest

from app.calendar import calendar, is_open, previous_segment_end, prior_session_close, prior_session_day, segment_start
from app.catalog import instrument
from app.engine import Engine
from app.marketdata import Bar
from app.models import DomainError
from app.sizing import size_order
from tests.test_engine import ControlledProvider, request


@dataclass(frozen=True, slots=True)
class OpeningProvider(ControlledProvider):
    @override
    def history(self, symbol: str, *, retrieved_not_before: datetime | None = None) -> tuple[Bar, ...]:
        bars = super(OpeningProvider, self).history(symbol)
        return tuple(replace(bar, retrieved_at=retrieved_not_before or bar.retrieved_at) for bar in bars)


@pytest.mark.parametrize(
    ("symbol", "moment"),
    [
        ("600519.SS", "2026-10-22T01:31:00+00:00"),
        ("600519.SS", "2026-10-22T01:45:00+00:00"),
        ("600519.SS", "2026-10-22T05:01:00+00:00"),
        ("AAPL", "2026-01-05T14:33:00+00:00"),
        ("AAPL", "2026-07-06T13:33:00+00:00"),
        ("600519.SS", "2026-10-08T01:31:00+00:00"),
        ("AAPL", "2026-11-30T14:33:00+00:00"),
    ],
)
def test_limit_accepted_when_previous_segment_recent(engine: Engine, symbol: str, moment: str) -> None:
    # Given: 上一支持段结束前两分钟的完整样本，无本段样本。
    now = datetime.fromisoformat(moment)
    item = instrument(symbol)
    previous = previous_segment_end(item, now)
    assert previous is not None
    engine.provider = OpeningProvider(
        quote_time=previous - timedelta(minutes=2), history_day=prior_session_day(item, now)
    )
    engine.clock = lambda: now
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(100000)
        engine.store.save(connection, state)
    body = request().model_copy(update={"symbol": symbol})
    # When: 预览、数量计算和提交复用同一个prepare。
    preview = engine.preview(body, now)
    sized = size_order(engine, body, now, 25)
    order = engine.place(body, now)
    # Then: 受理仍从提交时刻生效，不使用上一段区间成交。
    assert order.status == "accepted"
    assert order.active_at == preview.active_at == now
    assert sized.suggested_quantity > 0
    assert not engine.store.read().ledger


@pytest.mark.parametrize(
    ("moment", "quote", "order_type"),
    [
        ("01:51", "2026-10-21T06:55:00+00:00", "limit"),
        ("01:31", "2026-10-21T06:36:00+00:00", "limit"),
        ("01:31", "2026-10-21T06:55:00+00:00", "market"),
        ("02:00", "2026-10-22T01:35:00+00:00", "limit"),
        ("05:21", "2026-10-22T03:30:00+00:00", "limit"),
    ],
)
def test_stale_order_rejected_outside_opening_limit_exception(
    engine: Engine, moment: str, quote: str, order_type: str
) -> None:
    # Given: 超宽限、上段样本已旧、市价或盘中样本过旧。
    now = datetime.fromisoformat(f"2026-10-22T{moment}:00+00:00")
    engine.provider = OpeningProvider(quote_time=datetime.fromisoformat(quote))
    body = request().model_copy(update={"order_type": order_type})
    # When: 提交新单。
    with pytest.raises(DomainError) as error:
        _ = engine.place(body, now)
    # Then: 不产生受理、预留或账本。
    assert error.value.code == "quote_stale"
    assert not engine.store.read().orders


def test_opening_grace_includes_twentieth_minute(engine: Engine) -> None:
    # Given: 开段恰满20分钟，上段样本恰好位于容忍下界。
    now = datetime(2026, 10, 22, 1, 50, tzinfo=UTC)
    engine.provider = OpeningProvider(quote_time=datetime(2026, 10, 21, 6, 37, tzinfo=UTC))
    # When: 提交限价单。
    order = engine.place(request(), now)
    # Then: 闭合容忍边界受理。
    assert order.status == "accepted"


@pytest.mark.parametrize("symbol", ["600519.SS", "AAPL", "0700.HK"])
def test_segment_functions_match_open_minutes_through_2026(symbol: str) -> None:
    # Given: 全年逐日、每分钟抽样；预期支持段由交易所当日日程独立构造。
    item = instrument(symbol)
    cal = calendar(item.market)
    day = datetime(2026, 1, 1, tzinfo=cal.tz)
    finish = datetime(2027, 1, 1, tzinfo=cal.tz)
    minute = timedelta(minutes=1)
    while day < finish:
        local = day
        session = cal.date_to_session(str(local.date()), direction="next")
        opening = cal.session_open(session).to_pydatetime()
        closing = cal.session_close(session).to_pydatetime()
        if item.market == "CN":
            closing -= timedelta(minutes=3)
        previous_close = prior_session_close(item, opening)
        if item.market == "CN":
            previous_close -= timedelta(minutes=3)
        for offset in range(1440):
            at = local + offset * minute
            start = segment_start(item, at)
            prior = previous_segment_end(item, at)
            opened = is_open(item, at)
            assert (start is not None) == opened
            assert (prior is not None) == opened
            if opened:
                assert start is not None
                afternoon = item.market in {"CN", "HK"} and at.hour >= 13
                expected_start = at.replace(hour=13, minute=0) if afternoon else opening
                expected_prior = (
                    (at.replace(hour=11, minute=30) if item.market == "CN" else at.replace(hour=12, minute=0))
                    if afternoon
                    else previous_close
                )
                assert start == expected_start
                assert prior == expected_prior
                assert start <= at < closing
        day += timedelta(days=1)
