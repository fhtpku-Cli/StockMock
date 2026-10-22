"""使用真实解析器验证逐项准入和失败边界；仅替代网络字节。"""

from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from typing import Literal, final, override

import pytest
from pydantic import TypeAdapter

from app.admission_sources import candidate_rule, verify_candidate
from app.discovery_models import ObservationInstrument
from app.instrument_registry import validate_rule
from app.marketdata import MarketDataClient
from app.marketdata_models import Currency, MarketDataError


@final
class _Clock(datetime):
    @classmethod
    @override
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 22, 9, tzinfo=UTC)


def observation(code: str = "688981", name: str = "中芯国际") -> ObservationInstrument:
    return ObservationInstrument(
        symbol=f"{code}.SS", name=name, market="CN", currency="CNY", kind="stock", provider_code=f"sh{code}"
    )


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    for module in ("admission_sources", "instrument_import", "marketdata"):
        monkeypatch.setattr(f"app.{module}.datetime", _Clock)
    mode = ["normal"]

    def respond(_self: MarketDataClient, url: str) -> bytes:
        if "smartbox" in url:
            name = "*ST中芯" if mode[0] == "changed_status" else "中芯国际"
            return ("v_hint=" + TypeAdapter(str).dump_json(f"sh~688981~{name}~zxgj~GP-A-KCB").decode() + ";").encode(
                "gb18030"
            )
        if "/app/kline/kline?" in url:
            oldest = "2026-10-20" if mode[0] == "young" else "2026-01-02"
            return (
                '{"code":0,"data":{"sh688981":{"day":['
                f'["{oldest}","10","10","10","10","100"],'
                '["2026-10-22","10","10","10","10","100"]]}}}'
            ).encode()
        if "qt.gtimg" in url:
            fields = [""] * 49
            fields[1], fields[3], fields[30] = "中芯国际", "10", "20261022150000"
            fields[4], fields[47], fields[48] = "10", "11" if mode[0] == "wrong_limit" else "12", "8"
            return f'v_sh688981="{"~".join(fields)}";'.encode("gb18030")
        if "getKLineData" in url:
            volume = "0" if mode[0] == "empty_minute" else "100"
            return (
                'var _data=([{"day":"2026-10-22 15:00:00","open":"10","close":"10",'
                f'"high":"10","low":"10","volume":"{volume}"}}]);'
            ).encode()
        raise AssertionError(url)

    monkeypatch.setattr(MarketDataClient, "_get", respond)
    return mode


def test_admission_when_identity_maturity_and_complete_minutes_pass(network: list[str]) -> None:
    # Given: 网络边界提供有效搜索、跨90天日线和完整分钟，解析及核验均真实运行。
    assert network == ["normal"]
    item = observation()
    # When: 请求准入。
    rule = verify_candidate(item, MarketDataClient())
    # Then: 科创规则明确200股起、1股递增和20%，没有退回主板规则。
    assert (rule.tradable, rule.min_buy_quantity, rule.quantity_step, rule.price_limit_ratio) == (
        True,
        200,
        1,
        Decimal(".2"),
    )


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("young", "unverified_instrument_maturity"),
        ("wrong_limit", "unverified_price_limit"),
        ("changed_status", "unsupported_instrument_status"),
        ("empty_minute", "unverified_intraday"),
    ],
)
def test_admission_refused_when_evidence_fails(network: list[str], mode: str, error: str) -> None:
    # Given: 同一真实解析路径只有一项失败。
    network[0] = mode
    # When / Then: 明确失败，无可持久化规则返回。
    with pytest.raises(MarketDataError, match=error):
        _ = verify_candidate(observation(), MarketDataClient())


@pytest.mark.parametrize(
    ("symbol", "market", "kind", "error"),
    [
        ("00700.HK", "HK", "stock", "market_observation_only"),
        ("BRK.B", "US", "stock", "unsupported_provider_symbol"),
        ("TQQQ", "US", "ETF", "unverified_etf_rules"),
        ("511010.SS", "CN", "ETF", "unverified_etf_rules"),
        ("999999.SS", "CN", "stock", "unsupported_instrument_class"),
    ],
)
def test_rule_refused_when_category_unverified(
    symbol: str, market: Literal["CN", "HK", "US"], kind: Literal["stock", "ETF"], error: str
) -> None:
    # Given: 未核验产品不能通过自称股票或ETF绕过支持范围。
    item = ObservationInstrument(
        symbol=symbol,
        name="测试证券",
        market=market,
        kind=kind,
        currency=TypeAdapter(Currency).validate_python({"CN": "CNY", "HK": "HKD", "US": "USD"}[market]),
        provider_code={"CN": "sh", "HK": "hk", "US": "us"}[market] + symbol.split(".", maxsplit=1)[0],
    )
    # When / Then: 在外部调用前明确拒绝。
    with pytest.raises(MarketDataError, match=error):
        _ = candidate_rule(item)


def test_registry_rejects_star_when_minimum_is_weakened() -> None:
    # Given: 将已核验规则最小量错误改为100。
    rule = candidate_rule(observation()).model_copy(update={"min_buy_quantity": 100})
    # When / Then: 持久加载同样不能弱化科创最低数量。
    with pytest.raises(MarketDataError, match="invalid_instrument_rule"):
        _ = validate_rule(rule)
