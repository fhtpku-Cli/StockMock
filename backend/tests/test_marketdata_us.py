"""受控分钟夹具验证时间边界；不替代真实网络样本。"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.marketdata_models import MarketDataError
from app.marketdata_us import parse_us_intraday


def raw_bar(stamp: str, volume: str = "100", high: str = "12") -> bytes:
    row = {"d": stamp, "o": "10", "h": high, "l": "9", "c": "11.125", "v": volume}
    return f"var _data=({json.dumps([row])});".encode()


@pytest.mark.parametrize(("day", "hour"), [("2026-10-21", 13), ("2026-12-01", 14)])
def test_utc_interval_when_dst_changes(day: str, hour: int) -> None:
    # Given: 同一当地五分钟末端，夏冬时区不同。
    raw = raw_bar(f"{day} 09:35:00")
    now = datetime.fromisoformat(f"{day}T22:00:00+00:00")
    # When: 解析真实适配器边界。
    bar = parse_us_intraday(raw, "AAPL", now)[0]
    # Then: 起止间隔正确，价格未量化到报价网格。
    assert bar.market_time == datetime.fromisoformat(f"{day}T{hour}:35:00+00:00")
    assert bar.interval_start == datetime.fromisoformat(f"{day}T{hour}:30:00+00:00")
    assert (bar.price, bar.volume, bar.retrieved_at) == (Decimal("11.125"), Decimal(100), now)


@pytest.mark.parametrize(
    ("stamp", "volume"),
    [
        ("2026-10-21 09:35:00", "0"),
        ("2026-10-21 09:30:00", "100"),
        ("2026-10-21 16:05:00", "100"),
        ("2026-10-24 09:35:00", "100"),
        ("2026-10-21 10:05:00", "100"),
    ],
)
def test_no_candidate_when_zero_closed_or_unfinished(stamp: str, volume: str) -> None:
    # Given: 当前美东十点整，未完成区间不得用于委托。
    now = datetime(2026, 10, 21, 14, tzinfo=UTC)
    # When: 处理不适合撮合的区间。
    bars = parse_us_intraday(raw_bar(stamp, volume), "SPY", now)
    # Then: 无候选；不生成替代行情。
    assert bars == ()


@pytest.mark.parametrize("stamp", ["2026-10-21 09:36:00", "2026-10-21 09:35:01", "2026-10-21T09:35:00Z"])
def test_reject_when_timestamp_semantics_invalid(stamp: str) -> None:
    # Given: 标签不能解释成当地完整五分钟末端。
    raw = raw_bar(stamp)
    # When / Then: 必须拒绝，不能修整时间戳。
    with pytest.raises(MarketDataError, match="invalid_bar_time"):
        _ = parse_us_intraday(raw, "AAPL", datetime(2026, 10, 21, 22, tzinfo=UTC))


def test_reject_when_ohlc_inconsistent() -> None:
    # Given: 最高价低于收盘价。
    raw = raw_bar("2026-10-21 09:35:00", high="10")
    # When / Then: 源数据异常不能被默认为正常区间。
    with pytest.raises(MarketDataError, match="invalid_bar"):
        _ = parse_us_intraday(raw, "AAPL", datetime(2026, 10, 21, 22, tzinfo=UTC))
