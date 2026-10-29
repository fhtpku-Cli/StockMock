"""取得时刻约束仅刷新过早日线，错误与其他缓存保持原语义。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.marketdata import MarketDataClient, MarketDataError
from tests.test_marketdata import TransportFixture
from tests.test_marketdata_sdk_cache import SdkTransport


def daily(price: str) -> bytes:
    return (f'{{"code":0,"data":{{"sh600519":{{"day":[["2026-10-29","100","{price}","110","90","100"]]}}}}}}').encode()


def test_early_history_refreshes_once_under_concurrency() -> None:
    client = TransportFixture(daily("100"))
    old = client.history("600519.SS")
    boundary = datetime.now(UTC)
    assert old[0].retrieved_at < boundary
    client.raw = daily("105")
    client.block = True
    client.entered.clear()
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = [pool.submit(client.history, "600519.SS", retrieved_not_before=boundary) for _ in range(8)]
        assert client.entered.wait(timeout=3)
        client.release.set()
        bars = [item.result(timeout=3) for item in pending]
    assert client.calls == 2
    assert all(item[0].close == Decimal(105) and item[0].retrieved_at >= boundary for item in bars)
    assert len({item[0].retrieved_at for item in bars}) == 1
    assert client.history("600519.SS") == bars[0]
    assert client.history("600519.SS", retrieved_not_before=bars[0][0].retrieved_at) == bars[0]
    assert client.calls == 2


def test_default_and_qualified_cache_keep_original_time() -> None:
    client = TransportFixture(daily("100"))
    old = client.history("600519.SS")
    client.raw = daily("105")
    assert client.history("600519.SS") == old
    assert client.history("600519.SS", retrieved_not_before=old[0].retrieved_at - timedelta(seconds=1)) == old
    assert client.calls == 1


def test_refresh_failure_cannot_fall_back_and_recovers_after_fifteen_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    client = TransportFixture(daily("100"))
    _ = client.history("600519.SS")
    boundary = datetime.now(UTC)
    client.failure = True
    with pytest.raises(MarketDataError, match="http_error"):
        _ = client.history("600519.SS", retrieved_not_before=boundary)
    client.failure = False
    client.raw = daily("105")
    clock[0] = 14.999
    # 即使约束晚于错误取得时刻也不得打穿短错误缓存。
    later_boundary = datetime.now(UTC)
    with pytest.raises(MarketDataError, match="http_error"):
        _ = client.history("600519.SS", retrieved_not_before=later_boundary)
    with pytest.raises(MarketDataError, match="http_error"):
        _ = client.history("600519.SS")
    assert client.calls == 2
    clock[0] = 15
    bars = client.history("600519.SS", retrieved_not_before=boundary)
    assert bars[0].close == Decimal(105)
    assert bars[0].retrieved_at >= boundary
    assert client.calls == 3


def test_us_refresh_preserves_shared_sdk_and_other_symbol() -> None:
    client = SdkTransport()
    aapl = client.history("AAPL")
    msft = client.history("MSFT")
    boundary = datetime.now(UTC)
    refreshed = client.history("AAPL", retrieved_not_before=boundary)
    assert refreshed[0].retrieved_at >= boundary > aapl[0].retrieved_at
    assert client.history("MSFT") == msft
    assert (client.sdk_calls, client.daily_calls) == (1, 3)


def test_boundary_requires_timezone() -> None:
    with pytest.raises(MarketDataError, match="invalid_retrieval_boundary"):
        _ = MarketDataClient().history(
            "600519.SS", retrieved_not_before=datetime(2026, 10, 29, tzinfo=UTC).replace(tzinfo=None)
        )
