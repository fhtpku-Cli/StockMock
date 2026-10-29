"""以固定交易日历事实验证展示边界，不调用交易受理。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from exchange_calendars import Timestamp
from pydantic import ValidationError

from app.calendar import calendar
from app.market_session import project
from app.market_session_models import Market, MarketSessionView, Phase

# 独立固定预期采用 UTC，覆盖五种边界与半日/DST；不从被测投影计算预期。
DAYS: Final[list[tuple[Market, str, tuple[tuple[str, str], ...], str, str | None]]] = [
    ("CN", "2026-10-29", (("01:30", "03:30"), ("05:00", "06:57")), "07:00", "2026-10-30T01:30:00Z"),
    ("CN", "2026-12-31", (("01:30", "03:30"), ("05:00", "06:57")), "07:00", None),
    ("HK", "2026-10-29", (("01:30", "04:00"), ("05:00", "08:00")), "08:00", "2026-10-30T01:30:00Z"),
    ("HK", "2026-12-24", (("01:30", "04:00"),), "04:00", "2026-12-28T01:30:00Z"),
    ("US", "2026-03-06", (("14:30", "21:00"),), "21:00", "2026-03-09T13:30:00Z"),
    ("US", "2026-03-09", (("13:30", "20:00"),), "20:00", "2026-03-10T13:30:00Z"),
    ("US", "2026-10-30", (("13:30", "20:00"),), "20:00", "2026-11-02T14:30:00Z"),
    ("US", "2026-11-02", (("14:30", "21:00"),), "21:00", "2026-11-03T14:30:00Z"),
    ("US", "2026-11-27", (("14:30", "18:00"),), "18:00", "2026-11-30T14:30:00Z"),
]


def cases() -> list[tuple[Market, datetime, Phase, tuple[datetime, datetime] | None, datetime | None]]:
    result: list[tuple[Market, datetime, Phase, tuple[datetime, datetime] | None, datetime | None]] = []
    for market, day, slots, close, tomorrow in DAYS:
        periods = tuple(
            (datetime.fromisoformat(f"{day}T{s}:00Z"), datetime.fromisoformat(f"{day}T{e}:00Z")) for s, e in slots
        )
        actual_close = datetime.fromisoformat(f"{day}T{close}:00Z")
        for boundary in sorted({actual_close, *(stamp for pair in periods for stamp in pair)}):
            for delta in (-1000000, -1, 0, 1, 1000000):
                now = boundary + timedelta(microseconds=delta)
                current = next((p for p in periods if p[0] <= now < p[1]), None)
                following = next(
                    (p[0] for p in periods if p[0] > now), datetime.fromisoformat(tomorrow) if tomorrow else None
                )
                phase: Phase = "supported"
                if current is None:
                    if now < periods[0][0]:
                        phase = "before_open"
                    elif now >= actual_close:
                        phase = "after_hours"
                    elif now >= periods[-1][1]:
                        phase = "unsupported_closing"
                    else:
                        phase = "break"
                result.append((market, now, phase, current, following))
    return result


@pytest.mark.parametrize(("market", "now", "phase", "current", "following"), cases())
def test_fixed_boundary_when_query_is_near_transition(
    market: Market,
    now: datetime,
    phase: Phase,
    current: tuple[datetime, datetime] | None,
    following: datetime | None,
) -> None:
    # Given: 固定日历事实及边界前后 1 秒/1 微秒/等号。
    # When: 读取独立支持时段。
    view = project(market, now)
    # Then: 状态、当前段和严格未来首段准确，不按分钟舍入。
    assert view.phase == phase
    assert ((view.current_period.start, view.current_period.end) if view.current_period else None) == current
    assert (view.next_period.start if view.next_period else None) == following
    assert view.as_of == now


@pytest.mark.parametrize(
    ("market", "now", "phase", "following"),
    [
        ("CN", "2026-10-10T02:00:00Z", "non_session_day", "2026-10-12T01:30:00Z"),
        ("CN", "2026-10-01T02:00:00Z", "non_session_day", "2026-10-08T01:30:00Z"),
        ("CN", "2027-01-01T02:00:00Z", "unknown", None),
        ("US", "2026-11-26T15:00:00Z", "non_session_day", "2026-11-27T14:30:00Z"),
        ("US", "2026-10-30T00:30:00+08:00", "supported", "2026-10-30T13:30:00Z"),
    ],
)
def test_calendar_dates_when_weekday_assumption_is_wrong(
    market: Market,
    now: str,
    phase: Phase,
    following: str | None,
) -> None:
    # Given: 调休周六、假日、覆盖外或纽约跨日查询。
    # When: 查询交易所当地日期。
    view = project(market, datetime.fromisoformat(now))
    # Then: 使用交易日历和市场当地日，不猜测工作日。
    assert view.phase == phase
    assert (view.next_period.start if view.next_period else None) == (
        datetime.fromisoformat(following) if following else None
    )
    if market == "US" and phase == "supported":
        assert str(view.market_date) == "2026-10-29"


@pytest.mark.parametrize(
    "method", ["session_open", "session_close", "session_break_start", "session_break_end", "date_to_session"]
)
def test_current_failure_and_recovery_when_calendar_raises(
    monkeypatch: pytest.MonkeyPatch, method: str, caplog: pytest.LogCaptureFixture
) -> None:
    # Given: 当前日历边界抛错，异常内容不可泄露。
    cal = calendar("CN")
    now = datetime.fromisoformat("2026-10-29T02:00:00Z")

    def fail(*_args: str, **_kwargs: str) -> None:
        raise RuntimeError("private-calendar-detail")

    with monkeypatch.context() as patch:
        patch.setattr(cal, method, fail)
        # When: 当前日历不可用。
        view = project("CN", now)
    # Then: 整体未知、安全类别可查，恢复后正常。
    assert view.phase == "unknown"
    assert view.current_period is None
    assert view.next_period is None
    assert view.reason == "calendar_unavailable"
    assert "RuntimeError" in caplog.text
    assert "private-calendar-detail" not in caplog.text
    assert project("CN", now).phase == "supported"


@pytest.mark.parametrize("method", ["session_break_start", "session_break_end"])
def test_bad_break_when_only_one_end_is_missing(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    # Given: 半日 NaT 仅出现在正常 CN 午休的一端。
    hk = calendar("HK")
    missing = hk.session_break_start(hk.date_to_session("2026-12-24", direction="none"))

    def missing_break(_session: Timestamp) -> Timestamp:
        return missing

    monkeypatch.setattr(calendar("CN"), method, missing_break)
    # When: 投影读取不完整午休结构。
    view = project("CN", datetime.fromisoformat("2026-10-29T02:00:00Z"))
    # Then: 不能当作全天连续支持。
    assert view.phase == "unknown"


def test_bad_break_when_reversed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 午休起点晚于结束。
    cal = calendar("CN")
    monkeypatch.setattr(cal, "session_break_start", cal.session_close)
    # When: 投影读取反序午休。
    view = project("CN", datetime.fromisoformat("2026-10-29T02:00:00Z"))
    # Then: 整体未知。
    assert view.phase == "unknown"


def test_future_failure_when_current_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 当前下午支持段有效，仅下一交易日查询抛错。
    def fail(_session: str) -> None:
        raise RuntimeError("private-future-detail")

    monkeypatch.setattr(calendar("CN"), "next_session", fail)
    # When: 查询当前支持时段。
    view = project("CN", datetime.fromisoformat("2026-10-29T06:00:00Z"))
    # Then: 保留已知当前，不可用的未来仅为 null。
    assert view.phase == "supported"
    assert view.current_period is not None
    assert view.next_period is None
    assert view.reason is None


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("market", "JP"),
        ("market_timezone", "America/New_York"),
        ("market_date", "2026-10-30"),
        ("as_of", "2026-10-29T02:00:00"),
        ("phase", "open"),
        ("phase", "unknown"),
        ("current_period", None),
        ("reason", "calendar_unavailable"),
        ("current_period", {"start": "2026-10-29T03:30:00Z", "end": "2026-10-29T01:30:00Z"}),
        ("current_period", {"start": "2026-10-29T03:30:00Z", "end": "2026-10-29T04:30:00Z"}),
        ("next_period", {"start": "2026-10-29T01:30:00Z", "end": "2026-10-29T03:30:00Z"}),
        ("next_period", {"start": "2026-10-29T02:30:00Z", "end": "2026-10-29T03:30:00Z"}),
    ],
)
def test_model_rejects_when_response_fields_conflict(field: str, bad: str | dict[str, str] | None) -> None:
    # Given: 有效响应中注入单个矛盾字段。
    raw = project("CN", datetime.fromisoformat("2026-10-29T02:00:00Z")).model_dump(mode="json")
    raw[field] = bad
    # When / Then: 边界解析必须拒绝。
    with pytest.raises(ValidationError):
        _ = MarketSessionView.model_validate(raw)


def test_model_accepts_when_offsets_differ_and_extra_fields_exist() -> None:
    # Given: 同一绝对时刻使用北京时间，额外字段为将来扩展。
    raw = project("CN", datetime.fromisoformat("2026-10-29T02:00:00Z")).model_dump(mode="json")
    raw["as_of"] = "2026-10-29T10:00:00+08:00"
    raw["extra"] = "ignored"
    # When: 解析合法扩展响应。
    view = MarketSessionView.model_validate(raw)
    # Then: 仍处于当前支持时段。
    assert view.phase == "supported"
    assert view.as_of == datetime.fromisoformat("2026-10-29T02:00:00Z")


@pytest.mark.parametrize(
    ("market", "now", "start", "end"),
    [
        ("CN", "2026-10-29T02:00:00Z", "2026-10-29T05:00:00Z", "2026-10-29T06:57:00Z"),
        ("CN", "2026-10-29T06:00:00Z", "2026-10-30T01:30:00Z", "2026-10-30T03:30:00Z"),
        ("HK", "2026-12-23T08:00:00Z", "2026-12-24T01:30:00Z", "2026-12-24T04:00:00Z"),
        ("US", "2026-11-26T15:00:00Z", "2026-11-27T14:30:00Z", "2026-11-27T18:00:00Z"),
        ("US", "2026-03-06T21:00:00Z", "2026-03-09T13:30:00Z", "2026-03-09T20:00:00Z"),
        ("US", "2026-10-30T20:00:00Z", "2026-11-02T14:30:00Z", "2026-11-02T21:00:00Z"),
    ],
)
def test_next_period_end_when_future_session_is_short_or_dst_changes(
    market: Market,
    now: str,
    start: str,
    end: str,
) -> None:
    # Given: 下一段包含午休、提前收盘或夏令时切换。
    # When: 查询完整下一支持区间。
    view = project(market, datetime.fromisoformat(now))
    # Then: 起止均按实际日历，不只检查开盘。
    assert view.next_period is not None
    assert view.next_period.start == datetime.fromisoformat(start)
    assert view.next_period.end == datetime.fromisoformat(end)
