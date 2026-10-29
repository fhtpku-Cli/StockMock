"""0.4.5 日线受理、首区间投影与真实 SQLite 安全回归。"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import override

import pytest

from app.calendar import first_candidate_interval, prior_session_day
from app.catalog import instrument
from app.engine import Engine
from app.marketdata import Bar
from app.models import DomainError
from app.order_views import order_view
from app.sizing import size_order
from app.storage import Store
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_personal import held

CLOSE = NOW.replace(hour=7)


@dataclass(frozen=True, slots=True)
class DailyProvider(ControlledProvider):
    bars: tuple[Bar, ...] = ()

    @override
    def history(self, symbol: str, *, retrieved_not_before: datetime | None = None) -> tuple[Bar, ...]:
        return self.bars


def daily(at: datetime = CLOSE) -> Bar:
    return replace(ControlledProvider().history("600519.SS")[0], day=NOW.date(), close=Decimal(105), retrieved_at=at)


def candidate(tmp_path: Path, bars: tuple[Bar, ...], now: datetime) -> Engine:
    return Engine(Store(tmp_path / "ledger.sqlite3"), DailyProvider(quote_time=CLOSE, bars=bars), clock=lambda: now)


@pytest.mark.parametrize(
    "now", [CLOSE - timedelta(minutes=3), CLOSE - timedelta(minutes=2), CLOSE - timedelta(seconds=1)]
)
def test_unclosed_reference_rejected_before_history(tmp_path: Path, now: datetime) -> None:
    engine = candidate(tmp_path, (daily(),), now)
    engine.provider = DailyProvider(quote_time=now, bars=(daily(),))
    before = engine.store.read()
    with pytest.raises(DomainError) as caught:
        _ = engine.place(request(), now)
    assert caught.value.code == "reference_not_closed"
    assert engine.store.read() == before


@pytest.mark.parametrize(
    "bad",
    [
        "old",
        "future",
        "naive",
        "symbol",
        "currency",
        "source",
        "zero",
        "negative",
        "nan",
        "infinity",
        "day",
        "empty",
    ],
)
def test_reference_failure_keeps_account_unchanged(tmp_path: Path, bad: str) -> None:
    now = CLOSE + timedelta(minutes=10)
    bar = daily()
    changes = {
        "old": {"retrieved_at": CLOSE - timedelta(seconds=1)},
        "future": {"retrieved_at": now + timedelta(seconds=1)},
        "naive": {"retrieved_at": CLOSE.replace(tzinfo=None)},
        "symbol": {"symbol": "000001.SZ"},
        "currency": {"currency": "USD"},
        "source": {"source": "wrong"},
        "zero": {"close": Decimal(0)},
        "negative": {"close": Decimal(-1)},
        "nan": {"close": Decimal("NaN")},
        "infinity": {"close": Decimal("Infinity")},
        "day": {"day": NOW.date() - timedelta(days=1)},
        "empty": {},
    }
    bars = () if bad == "empty" else (replace(bar, **changes[bad]),)
    engine = candidate(tmp_path, bars, now)
    before = engine.store.read()
    with pytest.raises(DomainError) as caught:
        _ = engine.place(request(), now)
    assert caught.value.code in {"reference_unavailable", "reference_unverified"}
    assert engine.store.read() == before


def test_preview_place_sizing_share_postclose_reference(tmp_path: Path) -> None:
    engine = candidate(tmp_path, (daily(),), CLOSE)
    payload = request().model_copy(update={"limit_price": Decimal(112)})
    preview = engine.preview(payload, CLOSE)
    sizing = size_order(engine, payload, CLOSE, 100)
    order = engine.place(payload, CLOSE)
    assert preview.price_bound == 112
    assert sizing.max_quantity > 0
    assert (order.daily_lower, order.daily_upper) == (Decimal("94.50"), Decimal("115.50"))
    assert engine.place(payload, CLOSE) == order
    assert len(engine.store.read().orders) == 1


@pytest.mark.parametrize(
    ("price", "side", "filled"),
    [
        ("94.49", "buy", False),
        ("94.50", "buy", True),
        ("94.51", "buy", True),
        ("115.49", "buy", True),
        ("115.50", "buy", False),
        ("115.51", "buy", False),
    ],
)
def test_saved_bound_controls_next_session_matching(tmp_path: Path, price: str, side: str, *, filled: bool) -> None:
    engine = candidate(tmp_path, (daily(),), CLOSE)
    order = engine.place(request().model_copy(update={"limit_price": Decimal("115.50"), "side": side}), CLOSE)
    engine.provider = DailyProvider(quote_time=CLOSE, bars=(replace(daily(), close=Decimal(200)),))
    end = order.active_at + timedelta(minutes=5)
    q = replace(sample(engine, order, price), market_time=end, retrieved_at=end, interval_start=order.active_at)
    engine.process(order.symbol, (q,), end)
    state = engine.store.read()
    assert (state.orders[0].status == "filled") == filled
    assert (state.orders[0].daily_lower, state.orders[0].daily_upper) == (Decimal("94.50"), Decimal("115.50"))
    assert len(state.ledger) == (2 if filled else 0)
    assert len(state.lots) == (1 if filled else 0)
    engine.process(order.symbol, (replace(q, price=Decimal(100)),), end + timedelta(minutes=1))
    assert engine.store.read() == state


@pytest.mark.parametrize(
    ("active", "cutoff", "expected"),
    [
        ("2026-10-22T02:01:00+00:00", "2026-10-22T06:57:00+00:00", "2026-10-22T02:05:00+00:00"),
        ("2026-10-22T02:05:00+00:00", "2026-10-22T06:57:00+00:00", "2026-10-22T02:05:00+00:00"),
        ("2026-10-22T02:05:00.000001+00:00", "2026-10-22T06:57:00+00:00", "2026-10-22T02:10:00+00:00"),
        ("2026-10-22T03:28:00+00:00", "2026-10-22T06:57:00+00:00", "2026-10-22T05:00:00+00:00"),
        ("2026-10-22T06:53:00+00:00", "2026-10-22T06:57:00+00:00", None),
        ("2026-10-22T02:05:00+00:00", "2026-10-22T02:10:00+00:00", "2026-10-22T02:05:00+00:00"),
        ("2026-10-22T02:05:00+00:00", "2026-10-22T02:09:59.999999+00:00", None),
    ],
)
def test_first_cn_interval(active: str, cutoff: str, expected: str | None) -> None:
    result = first_candidate_interval(
        instrument("600519.SS"), datetime.fromisoformat(active), datetime.fromisoformat(cutoff)
    )
    assert result == (
        None
        if expected is None
        else (datetime.fromisoformat(expected), datetime.fromisoformat(expected) + timedelta(minutes=5))
    )


@pytest.mark.parametrize(("day", "hour"), [("2026-07-06", 13), ("2026-11-30", 14)])
def test_us_dst_opening_interval(day: str, hour: int) -> None:
    opening = datetime.fromisoformat(day).replace(hour=hour, minute=30, tzinfo=UTC)
    assert first_candidate_interval(instrument("AAPL"), opening, opening + timedelta(minutes=5)) == (
        opening,
        opening + timedelta(minutes=5),
    )


def test_us_early_close_and_holiday() -> None:
    close = datetime(2026, 11, 27, 18, tzinfo=UTC)
    assert first_candidate_interval(instrument("AAPL"), close - timedelta(minutes=5), close) == (
        close - timedelta(minutes=5),
        close,
    )
    holiday = datetime(2026, 11, 26, 15, tzinfo=UTC)
    assert first_candidate_interval(instrument("AAPL"), holiday, holiday + timedelta(hours=1)) is None


@pytest.mark.parametrize(("day", "prior"), [("2026-11-02", "2026-10-30"), ("2026-10-08", "2026-09-30")])
def test_prior_session_across_weekend_month_and_holiday(day: str, prior: str) -> None:
    assert prior_session_day(
        instrument("600519.SS"), datetime.fromisoformat(day).replace(tzinfo=UTC)
    ) == date.fromisoformat(prior)


def test_projection_cancel_recomputes_without_persisting(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    original = engine.store.read().model_dump_json()
    assert order_view(order, engine.catalog).first_candidate_interval.status == "available"
    assert engine.store.read().model_dump_json() == original
    cancelled = engine.cancel(order.id, NOW + timedelta(minutes=4))
    assert order_view(cancelled, engine.catalog).first_candidate_interval.status == "none"
    assert "first_candidate_interval" not in engine.store.read().model_dump_json()
    unknown = order.model_copy(update={"symbol": "UNKNOWN"})
    assert order_view(unknown).first_candidate_interval.status == "unknown"


@pytest.mark.parametrize(
    ("price", "filled"), [("94.49", False), ("94.50", False), ("94.51", True), ("115.50", True), ("115.51", False)]
)
def test_sell_daily_boundary_keeps_downlimit_protection(tmp_path: Path, price: str, *, filled: bool) -> None:
    engine = candidate(tmp_path, (daily(),), CLOSE)
    held(engine)
    order = engine.place(request().model_copy(update={"side": "sell", "limit_price": Decimal("94.50")}), CLOSE)
    end = order.active_at + timedelta(minutes=5)
    q = replace(sample(engine, order, price), market_time=end, retrieved_at=end, interval_start=order.active_at)
    engine.process(order.symbol, (q,), end)
    state = engine.store.read()
    assert (state.orders[0].status == "filled") == filled
    assert len(state.ledger) == (2 if filled else 0)
    assert sum(lot.quantity for lot in state.lots) == (0 if filled else 100)


def test_unknown_calendar_still_projects_old_order(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    future = datetime(2100, 1, 1, tzinfo=UTC)
    old = order.model_copy(update={"active_at": future, "expires_at": future + timedelta(hours=1)})
    assert order_view(old).first_candidate_interval.status == "unknown"
    assert order.active_at == NOW
