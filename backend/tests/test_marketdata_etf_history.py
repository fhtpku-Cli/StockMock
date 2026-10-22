"""ETF真实请求路径与原始价格边界的回归验证。"""

from decimal import Decimal

import pytest

from app.admission_sources import AdmissionSources
from app.discovery_models import ObservationInstrument
from app.marketdata import MarketDataClient
from app.marketdata_models import MarketDataError


def etf() -> ObservationInstrument:
    return ObservationInstrument(
        symbol="512880.SS", provider_code="sh512880", name="证券ETF", market="CN", currency="CNY", kind="ETF"
    )


@pytest.mark.parametrize("maturity", [False, True])
def test_etf_uses_raw_endpoint_when_chart_or_maturity_requested(
    monkeypatch: pytest.MonkeyPatch, *, maturity: bool
) -> None:
    # Given: 旧fqkline地址失败，只有同供应商原始地址能返回真实解析结构。
    requested: list[str] = []

    def respond(_self: MarketDataClient, url: str) -> bytes:
        requested.append(url)
        if "/app/kline/kline?" not in url:
            raise MarketDataError("http_error", "501")
        return (
            b'{"code":0,"data":{"sh512880":{"day":[["2026-10-22","1.0","1.1","1.2","0.9","100"]],'
            b'"qfqday":[["2026-10-22","9","9","9","9","100"]]}}}'
        )

    monkeypatch.setattr(MarketDataClient, "_get", respond)
    # When: 两个真实公共方法分别取图表和成熟性历史。
    bars = AdmissionSources().maturity_history(etf()) if maturity else MarketDataClient().history(etf())
    # Then: 只发起对应长度的原始路径请求且只解析day，不把复权价格当原价。
    count = 252 if maturity else 30
    assert requested == [f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=sh512880,day,,,{count}"]
    assert (len(bars), bars[0].symbol, bars[0].close) == (1, "512880.SS", Decimal("1.1"))


def test_etf_refuses_adjusted_only_response(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 同一原始URL返回仅含复权数据的异常结果。
    def respond(_self: MarketDataClient, _url: str) -> bytes:
        return b'{"code":0,"data":{"sh512880":{"qfqday":[["2026-10-22","9","9","9","9","100"]]}}}'

    monkeypatch.setattr(MarketDataClient, "_get", respond)
    # When / Then: 不拼接、不造原始价格。
    with pytest.raises(MarketDataError, match="history_unavailable"):
        _ = MarketDataClient().history(etf())


@pytest.mark.parametrize("provider", ["sh600000", "sh688981", "sz300750"])
@pytest.mark.parametrize("maturity", [False, True])
def test_cn_stock_uses_raw_endpoint_when_old_endpoint_fails(
    monkeypatch: pytest.MonkeyPatch, provider: str, *, maturity: bool
) -> None:
    # Given: 各板普通股旧接口失败，原始接口返回未复权价格。
    item = ObservationInstrument(
        symbol=provider[2:] + (".SS" if provider.startswith("sh") else ".SZ"),
        provider_code=provider,
        name="普通股票",
        market="CN",
        currency="CNY",
        kind="stock",
    )
    requested: list[str] = []

    def respond(_self: MarketDataClient, url: str) -> bytes:
        requested.append(url)
        if "/app/kline/kline?" not in url:
            raise MarketDataError("http_error", "501")
        return ('{"code":0,"data":{"' + provider + '":{"day":[["2026-10-22","10","11","12","9","100"]]}}}').encode()

    monkeypatch.setattr(MarketDataClient, "_get", respond)
    # When: 真实图表或成熟性入口执行网络分派和解析。
    bars = AdmissionSources().maturity_history(item) if maturity else MarketDataClient().history(item)
    # Then: 原始路径成功，未请求已经失败的复权接口。
    count = 252 if maturity else 30
    assert requested == [f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={provider},day,,,{count}"]
    assert (bars[0].symbol, bars[0].close, bars[0].volume) == (item.symbol, Decimal(11), Decimal(100))
