"""0.5.0 展示读取：受控夹具验证昨收解析、图表校验与缓存共用，不连接真实行情。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import override

import pytest

from app.marketdata import MarketDataClient, MarketDataError
from app.marketdata_models import Bar
from app.marketdata_reference import checked_chart_bars, parse_previous_close


def quote_raw(code: str, stamp: str, price: str = "101.00", previous: str = "100.00") -> bytes:
    fields = [""] * 50
    fields[3], fields[4], fields[30] = price, previous, stamp
    return f'v_{code}="{"~".join(fields)}";'.encode("gb18030")


def kline_raw(code: str, days: list[str], close: str = "100") -> bytes:
    rows = ",".join(f'["{day}","100","{close}","110","90","1000"]' for day in days)
    return f'{{"code":0,"data":{{"{code}":{{"day":[{rows}]}}}}}}'.encode()


class Recorder(MarketDataClient):
    """只替换网络边界并记录地址；缓存、解析与校验均为真实实现。"""

    def __init__(self, responses: dict[str, bytes]) -> None:
        super().__init__()
        self.responses: dict[str, bytes] = responses
        self.urls: list[str] = []

    @override
    def _get(self, url: str) -> bytes:
        self.urls.append(url)
        return next(body for key, body in self.responses.items() if key in url)


@pytest.mark.parametrize(
    ("previous", "expected"),
    [("100.50", Decimal("100.50")), ("", None), ("0", None), ("abc", None), ("NaN", None), ("-1", None)],
)
def test_previous_close_parses_only_finite_positive(previous: str, expected: Decimal | None) -> None:
    assert parse_previous_close(quote_raw("sh600519", "20261030150000", previous=previous), "sh600519") == expected


def test_previous_close_missing_field_returns_none() -> None:
    raw = 'v_sh600519="1~name~600519~101";'.encode("gb18030")
    assert parse_previous_close(raw, "sh600519") is None
    assert parse_previous_close(b"garbage", "sh600519") is None
    assert parse_previous_close(b"\xff\xff", "sh600519") is None


def bar(day: date) -> Bar:
    now = datetime(2026, 10, 30, tzinfo=UTC)
    one = Decimal(1)
    return Bar("600519.SS", day, one, one, one, one, one, "CNY", now, now, "tencent-public")


def test_chart_bars_reject_duplicate_and_unsorted_days() -> None:
    days = [date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)]
    ordered = tuple(bar(day) for day in days)
    assert checked_chart_bars(ordered) == ordered
    for broken in ((ordered[0], ordered[0]), (ordered[1], ordered[0], ordered[2])):
        with pytest.raises(MarketDataError, match="invalid_chart_history"):
            _ = checked_chart_bars(broken)


def test_quote_reference_uses_one_load_and_shares_quote_cache() -> None:
    client = Recorder({"qt.gtimg.cn": quote_raw("sh600519", "20261030150000")})
    reference = client.quote_reference("600519.SS")
    quote = client.quote("600519.SS")
    again = client.quote_reference("600519.SS")
    assert client.urls == ["https://qt.gtimg.cn/q=sh600519"]
    assert reference.quote == quote == again.quote
    assert (reference.quote.price, reference.previous_close) == (Decimal("101.00"), Decimal("100.00"))


def test_quote_reference_after_quote_reuses_cached_response() -> None:
    client = Recorder({"qt.gtimg.cn": quote_raw("sh600519", "20261030150000")})
    quote = client.quote("600519.SS")
    reference = client.quote_reference("600519.SS")
    assert len(client.urls) == 1
    assert reference.quote == quote


def test_quote_reference_fails_like_quote_when_price_invalid() -> None:
    client = Recorder({"qt.gtimg.cn": quote_raw("sh600519", "20261030150000", price="0")})
    with pytest.raises(MarketDataError):
        _ = client.quote_reference("600519.SS")


def test_chart_history_requests_252_and_history_keeps_30() -> None:
    days = ["2026-10-28", "2026-10-29", "2026-10-30"]
    client = Recorder({"kline": kline_raw("sh600519", days)})
    chart = client.chart_history("600519.SS")
    history = client.history("600519.SS")
    assert [url.rsplit(",", 1)[1] for url in client.urls] == ["252", "30"]
    assert [row.day.isoformat() for row in chart] == days
    assert chart[0].retrieved_at == chart[-1].retrieved_at
    assert history[-1].day == chart[-1].day


def test_chart_history_retrieved_at_is_first_cached_retrieval() -> None:
    client = Recorder({"kline": kline_raw("sh600519", ["2026-10-29", "2026-10-30"])})
    first = client.chart_history("600519.SS")
    second = client.chart_history("600519.SS")
    assert len(client.urls) == 1
    assert first[0].retrieved_at == second[0].retrieved_at


def test_chart_history_rejects_duplicates_while_history_accepts_same_rows() -> None:
    days = ["2026-10-29", "2026-10-29", "2026-10-30"]
    client = Recorder({"kline": kline_raw("sh600519", days)})
    with pytest.raises(MarketDataError, match="invalid_chart_history"):
        _ = client.chart_history("600519.SS")
    assert len(client.history("600519.SS")) == len(days)
