"""上市证据日期边界与扩展目录交易约束的受控测试。"""

from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import final, override

import pytest

from app.instrument_import import ImportEntry, verify_entry
from app.instrument_rules import CATALOG_RULES
from app.instrument_universe import US_EXPANSION
from app.marketdata import MarketDataClient, MarketDataError


@final
class _Clock(datetime):
    @classmethod
    @override
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 22, 17, tzinfo=UTC)


def _entry() -> ImportEntry:
    rule = next(row for row in CATALOG_RULES if row.symbol == "AAPL").model_copy(update={"tradable": False})
    return ImportEntry(rule=rule, status="normal", product="ordinary_stock", metadata_source="https://www.apple.com/")


@pytest.mark.parametrize("both", [False, True])
def test_listing_evidence_rejected_when_missing_or_ambiguous(*, both: bool) -> None:
    # Given: 不提供日期，或同时提供两个含义不同的日期。
    entry = _entry()
    if both:
        entry = entry.model_copy(update={"listing_date": date(1980, 12, 12), "listed_on_or_before": date(2025, 1, 1)})
    # When / Then: 在发起网络请求前明确拒绝。
    with pytest.raises(MarketDataError, match="invalid_listing_evidence"):
        _ = verify_entry(entry, MarketDataClient())


@pytest.mark.parametrize("field", ["listing_date", "listed_on_or_before"])
def test_listing_evidence_rejected_when_younger_than_ninety_days(field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 官方资料只证明 89 天前已上市，无法排除新股。
    monkeypatch.setattr("app.instrument_import.datetime", _Clock)
    entry = _entry().model_copy(update={field: _Clock.now().date() - timedelta(days=89)})
    # When / Then: 精确日期和日期上界都保留原有排除期。
    with pytest.raises(MarketDataError, match="unverified_instrument_metadata"):
        _ = verify_entry(entry, MarketDataClient())


@pytest.mark.parametrize("field", ["listing_date", "listed_on_or_before"])
def test_import_accepts_evidence_when_ninety_days_and_actual_parser_agree(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 满 90 天，网络仅替代字节，实际报价解析器照常运行。
    monkeypatch.setattr("app.instrument_import.datetime", _Clock)
    monkeypatch.setattr("app.marketdata.datetime", _Clock)
    entry = _entry().model_copy(update={field: _Clock.now().date() - timedelta(days=90)})
    fields = [""] * 31
    fields[3], fields[30] = "100.5", "2026-10-22 12:59:00"
    raw = f'v_usAAPL="{"~".join(fields)}";'.encode()

    def respond(_self: MarketDataClient, _url: str) -> bytes:
        return raw

    monkeypatch.setattr(MarketDataClient, "_get", respond)
    # When: 完整元数据及真实解析边界核验。
    rule = verify_entry(entry, MarketDataClient())
    # Then: 接受成熟证券，保留本人明确提供的观察限制。
    assert rule.symbol == "AAPL"
    assert not rule.tradable


def test_expansion_rules_when_loaded_keep_conservative_trade_constraints() -> None:
    # Given: 由正式目录加载新增证券，而非另建演示列表。
    symbols = {entry.symbol for entry in US_EXPANSION}
    # When: 按公开代码索引取得新规则。
    rules = {rule.symbol: rule for rule in CATALOG_RULES if rule.symbol in symbols}
    # Then: 覆盖无别名冲突，所有新增证券保留整股及一美元以上网格约束。
    assert set(rules) == symbols
    assert len({rule.provider_code for rule in CATALOG_RULES}) == len(CATALOG_RULES)
    for symbol, rule in rules.items():
        assert (rule.provider_code, rule.market, rule.currency) == (f"us{symbol}", "US", "USD")
        assert (rule.lot_size, rule.tick_size, rule.tick_rule, rule.settlement_days) == (1, Decimal(".01"), "US", 0)
        assert rule.tradable
    assert all(not rule.tradable for rule in CATALOG_RULES if rule.market == "HK")
