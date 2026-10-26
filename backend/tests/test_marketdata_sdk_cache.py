"""SDK 内容缓存的失败恢复与共享边界；上游使用自制数据。"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from typing import final, override

import pytest

from app.marketdata import MarketDataClient, MarketDataError
from app.marketdata_daily import SDK_URL
from tests.test_marketdata_daily import sdk_fixture


@final
class SdkTransport(MarketDataClient):
    """仅替换下载边界，允许切换来源内容并计数真实缓存请求。"""

    def __init__(self) -> None:
        super().__init__()
        self.sdk = sdk_fixture(
            'return [{date:"2026-10-21T00:00:00Z",open:10,high:11,low:9,close:10,volume:10}];',
        )
        self.sdk_calls = 0
        self.daily_calls = 0
        self.http_failure = False
        self.daily_valid = True
        self.block = False
        self.entered = Event()
        self.release = Event()

    @override
    def _get(self, url: str) -> bytes:
        if url == SDK_URL:
            self.sdk_calls += 1
            self.entered.set()
            if self.block:
                assert self.release.wait(timeout=3)
            if self.http_failure:
                raise MarketDataError("http_error", "503")
            return self.sdk
        self.daily_calls += 1
        symbol = url.rsplit("klc_kl_", 1)[1].removesuffix(".js")
        return f'var KLC_KL_{symbol}="AAA";'.encode() if self.daily_valid else b"invalid daily"


@pytest.mark.parametrize("http_failure", [False, True])
def test_bad_sdk_recovers_after_short_cache_across_symbols(
    monkeypatch: pytest.MonkeyPatch, *, http_failure: bool
) -> None:
    # Given: 首次结构错误或 HTTP 错误；真实缓存、解码、解析照常运行。
    clock = [0.0]
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    client = SdkTransport()
    valid_sdk = client.sdk
    client.sdk = b"<html>temporarily unavailable</html>"
    client.http_failure = http_failure
    code = "http_error" if http_failure else "decoder_source_changed"
    with pytest.raises(MarketDataError, match=code):
        _ = client.history("AAPL")
    # When: 来源恢复，同一客户端和另一证券在十五秒前仍应共享失败。
    client.sdk, client.http_failure = valid_sdk, False
    clock[0] = 14.999
    with pytest.raises(MarketDataError, match=code):
        _ = client.history("MSFT")
    assert client.sdk_calls == 1
    clock[0] = 15
    # Then: 不必等一天即可恢复；两个证券共享新的有效 SDK。
    assert client.history("MSFT")[0].close == Decimal(10)
    assert client.history("AAPL")[0].symbol == "AAPL"
    assert client.sdk_calls == 2


def test_valid_sdk_retains_day_cache_and_original_fetch_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 合法 SDK 已缓存。
    clock = [0.0]
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    client = SdkTransport()
    initial = client.history("AAPL")
    # When: 日线自身缓存尚未过期，再读取保持最初取得时间。
    clock[0] = 15
    assert client.history("AAPL") == initial
    clock[0] = 86399
    _ = client.history("MSFT")
    assert client.sdk_calls == 1
    # Then: SDK 一天边界才重新下载。
    clock[0] = 86400
    _ = client.history("AAPL")
    assert client.sdk_calls == 2


def test_bad_daily_payload_preserves_valid_shared_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: SDK 合法，个别证券日线载荷错误。
    clock = [0.0]
    monkeypatch.setattr("app.marketdata.monotonic", lambda: clock[0])
    client = SdkTransport()
    client.daily_valid = False
    with pytest.raises(MarketDataError, match="invalid_daily_encoding"):
        _ = client.history("AAPL")
    # When: 另一证券日线正常，不删除或重取共享 SDK。
    client.daily_valid = True
    assert client.history("MSFT")[0].symbol == "MSFT"
    assert client.sdk_calls == 1
    # Then: 原坏日线继续保留自身 TTL，过期后能恢复且仍复用 SDK。
    clock[0] = 3600
    assert client.history("AAPL")[0].symbol == "AAPL"
    assert client.sdk_calls == 1


@pytest.mark.parametrize("valid", [False, True])
def test_concurrent_history_coalesces_shared_sdk(*, valid: bool) -> None:
    # Given: 八个跨证券请求等待同一首次下载。
    client = SdkTransport()
    client.block = True
    if not valid:
        client.sdk = b"<html>temporarily unavailable</html>"
    symbols = ["AAPL", "MSFT"] * 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = [pool.submit(client.history, symbol) for symbol in symbols]
        assert client.entered.wait(timeout=3)
        client.release.set()
        for future, symbol in zip(pending, symbols, strict=True):
            if valid:
                assert future.result(timeout=3)[0].symbol == symbol
            else:
                with pytest.raises(MarketDataError, match="decoder_source_changed"):
                    _ = future.result(timeout=3)
    # Then: 有效与错误内容均合并为一次下载，不制造假日线。
    assert client.sdk_calls == 1
    assert client.daily_calls == (2 if valid else 0)
