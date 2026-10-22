"""按需核验身份、交易规则和真实行情；成功结果才交给调用方持久化。"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final, assert_never

from app.admission_etfs import ETF_METADATA
from app.discovery import Discovery
from app.discovery_models import ObservationInstrument
from app.instrument_import import ImportEntry, verify_entry
from app.instrument_registry import validate_rule
from app.instrument_rules import InstrumentRule
from app.marketdata import MarketDataClient
from app.marketdata_models import Bar, MarketDataError
from app.marketdata_parsers import parse_history
from app.marketdata_urls import tencent_history_url

SSE_RULES: Final = "https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml"
SZSE_RULES: Final = "https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf"
US_RULES: Final = "https://www.sec.gov/newsroom/press-releases/2024-137"


class AdmissionSources(Discovery):
    """复用已有有界网络访问，只为待启用的单个境内标的请求长日线。"""

    def maturity_history(self, item: ObservationInstrument) -> tuple[Bar, ...]:
        """252 根未复权日线用于存在日期证明，不冒充 IPO 日期或成交依据。"""
        url = tencent_history_url(item, 252)
        raw, retrieved_at = self._load(url, 3600)
        return parse_history(raw, item, retrieved_at)

    def verify_price_limit(self, rule: InstrumentRule) -> None:
        """腾讯当日上下限与已确认规则交叉核对；无涨跌幅期及规则变化直接拒绝。"""
        raw, _ = self._load(f"https://qt.gtimg.cn/q={rule.provider_code}", 60)
        try:
            text = raw.decode("gb18030").strip()
            prefix = f'v_{rule.provider_code}="'
            if not text.startswith(prefix) or not text.endswith('";'):
                raise MarketDataError("unverified_price_limit", rule.symbol)
            fields = text[len(prefix) : -2].split("~")
            previous, upper, lower = (Decimal(fields[index]) for index in (4, 47, 48))
        except (UnicodeError, IndexError, InvalidOperation) as exc:
            raise MarketDataError("unverified_price_limit", rule.symbol) from exc
        if rule.price_limit_ratio is None or not all(
            value.is_finite() and value > 0 for value in (previous, upper, lower)
        ):
            raise MarketDataError("unverified_price_limit", rule.symbol)
        expected_upper = (previous * (1 + rule.price_limit_ratio)).quantize(rule.tick_size, rounding=ROUND_HALF_UP)
        expected_lower = (previous * (1 - rule.price_limit_ratio)).quantize(rule.tick_size, rounding=ROUND_HALF_UP)
        if (upper, lower) != (expected_upper, expected_lower):
            raise MarketDataError("unverified_price_limit", "实际涨跌幅与已核验规则不符，暂不开放")


def candidate_rule(item: ObservationInstrument) -> InstrumentRule:
    """构造受支持的规则；调用方仍必须执行身份、成熟性和行情核验。"""
    if item.name.upper().startswith(("N", "C", "ST", "*ST", "S*ST")) and item.market == "CN":
        raise MarketDataError("unsupported_instrument_status", item.symbol)
    match item.market:
        case "HK":
            raise MarketDataError("market_observation_only", "港股仅供观察，不开放模拟交易")
        case "CN":
            star = item.provider_code.startswith("sh688") and item.kind == "stock"
            growth = item.provider_code.startswith(("sz300", "sz301")) and item.kind == "stock"
            limit = Decimal(".2") if star or growth else Decimal(".1")
            source = SSE_RULES if item.provider_code.startswith("sh") else SZSE_RULES
            if item.kind == "ETF":
                metadata = ETF_METADATA.get(item.symbol)
                if metadata is None:
                    raise MarketDataError("unverified_etf_rules", "此 ETF 的投资类别和涨跌幅规则尚未核验")
                limit = metadata.price_limit_ratio
            rule = InstrumentRule(
                symbol=item.symbol,
                name=item.name,
                provider_code=item.provider_code,
                market="CN",
                currency=item.currency,
                kind=item.kind,
                lot_size=1 if star else 100,
                min_buy_quantity=200 if star else 100,
                quantity_step=1 if star else 100,
                tick_size=Decimal(".01") if item.kind == "stock" else Decimal(".001"),
                settlement_days=1,
                tradable=True,
                price_limit_ratio=limit,
                restriction="仅成熟普通股票及逐项核验的境内股票ETF；排除风险警示、退市及上市初期；公司行动人工核对",
                rule_source=source,
                verified_on=datetime.now(UTC).date(),
            )
        case "US":
            if not item.symbol.isascii() or not item.symbol.isalpha():
                raise MarketDataError("unsupported_provider_symbol", "行情供应商未核验此特殊代码，不自动改写映射")
            if item.kind == "ETF" and item.symbol not in ETF_METADATA:
                raise MarketDataError("unverified_etf_rules", "此 ETF 尚未确认属于非杠杆、非反向普通股票ETF")
            rule = InstrumentRule(
                symbol=item.symbol,
                name=item.name,
                provider_code=item.provider_code,
                market="US",
                currency=item.currency,
                kind=item.kind,
                lot_size=1,
                min_buy_quantity=1,
                quantity_step=1,
                tick_size=Decimal(".01"),
                settlement_days=0,
                tradable=True,
                tick_rule="US",
                restriction="常规时段整股模拟；≥1美元仅支持0.01价格网格；排除杠杆反向ETF；公司行动人工核对",
                rule_source=US_RULES,
                verified_on=datetime.now(UTC).date(),
            )
        case _:
            assert_never(item.market)
    return validate_rule(rule)


def verify_candidate(item: ObservationInstrument, client: MarketDataClient) -> InstrumentRule:
    """重新确认身份、90 天上市存在证据及报价/完整分钟/日线，失败不开放。"""
    _ = candidate_rule(item)
    sources = AdmissionSources()
    query = item.symbol.split(".")[0] if item.market == "CN" else item.symbol
    current = next((row for row in sources.search(query) if row.symbol == item.symbol), None)
    if current is None or (current.provider_code, current.market, current.currency, current.kind) != (
        item.provider_code,
        item.market,
        item.currency,
        item.kind,
    ):
        raise MarketDataError("instrument_identity_changed", "实时搜索无法确认相同证券身份和类别")
    rule = candidate_rule(current)
    if rule.market == "CN":
        sources.verify_price_limit(rule)
    history = sources.maturity_history(current) if current.market == "CN" else client.history(rule)
    today = datetime.now(UTC).date()
    if not history or any(
        row.symbol != rule.symbol or row.currency != rule.currency or row.day > today for row in history
    ):
        raise MarketDataError("unverified_history", rule.symbol)
    oldest, newest = min(row.day for row in history), max(row.day for row in history)
    if oldest > today - timedelta(days=90):
        raise MarketDataError("unverified_instrument_maturity", "真实日线无法证明该证券至少90天前已经上市")
    if newest < today - timedelta(days=7):
        raise MarketDataError("stale_history", rule.symbol)
    metadata_source = (
        "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
        if current.market == "CN"
        else f"https://finance.sina.com.cn/us_stock/company/hisdata/klc_kl_{rule.symbol}.js"
    )
    entry = ImportEntry(
        rule=rule,
        listed_on_or_before=oldest,
        status="normal",
        product="ordinary_stock" if rule.kind == "stock" else "unleveraged_equity_etf",
        metadata_source=ETF_METADATA[rule.symbol].source if rule.kind == "ETF" else metadata_source,
    )
    verified = verify_entry(entry, client)
    bars = client.intraday(verified)
    if not bars or any(
        row.symbol != rule.symbol
        or row.currency != rule.currency
        or not row.tradable
        or row.interval_start is None
        or row.interval_start >= row.market_time
        or row.market_time > row.retrieved_at
        or row.volume is None
        or row.volume <= 0
        for row in bars
    ):
        raise MarketDataError("unverified_intraday", rule.symbol)
    return verified
