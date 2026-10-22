from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import pytest

from app.discovery_models import ObservationInstrument
from app.marketdata import MarketDataClient
from app.marketdata_models import MarketDataError


@pytest.mark.parametrize(("code", "kind"), [("00700", "stock"), ("02800", "ETF")])
def test_hk_observation_history_uses_raw_day_and_local_date(
    monkeypatch: pytest.MonkeyPatch, code: str, kind: Literal["stock", "ETF"]
) -> None:
    # Given: 原始day与复权序列价格不同，旧接口明确失败。
    item = ObservationInstrument(
        symbol=f"{code}.HK",
        provider_code=f"hk{code}",
        name="观察证券",
        market="HK",
        currency="HKD",
        kind=kind,
    )
    requested: list[str] = []

    def respond(_self: MarketDataClient, url: str) -> bytes:
        requested.append(url)
        if "/app/kline/kline?" not in url:
            raise MarketDataError("http_error", "501")
        return (
            '{"code":0,"data":{"hk'
            + code
            + '":{"day":[["2026-10-22","10","11","12","9","12345"]],'
            + '"qfqday":[["2026-10-22","20","21","22","19","12345"]]}}}'
        ).encode()

    monkeypatch.setattr(MarketDataClient, "_get", respond)
    # When: 真实公共观察接口获取并解析日线。
    bars = MarketDataClient().history(item)
    # Then: 原价、港币、原生成交量及香港本地日期保留，观察权限不变。
    assert requested == [f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=hk{code},day,,,30"]
    assert (bars[0].close, bars[0].volume, bars[0].currency) == (Decimal(11), Decimal(12345), "HKD")
    assert bars[0].market_time == datetime(2026, 10, 21, 16, tzinfo=UTC)
    assert not item.tradable
