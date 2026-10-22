"""搜索观察标的不携带未经核验的交易规则。"""

from typing import Literal

from app.instrument_registry import resolve_rule
from app.instrument_rules import InstrumentRule
from app.models import Currency, Instrument, Model


class ObservationInstrument(Model):
    """仅由供应商搜索确认身份；不会成为交易目录成员。"""

    symbol: str
    name: str
    market: Literal["CN", "HK", "US"]
    currency: Currency
    kind: Literal["stock", "ETF"]
    provider_code: str
    tradable: Literal[False] = False
    restriction: str = "交易规则尚未核验，仅供观察；港股模拟交易仍需已核验行情权限"
    lot_size: None = None
    tick_size: None = None
    min_buy_quantity: None = None
    quantity_step: None = None
    settlement_days: None = None
    price_limit_ratio: None = None
    tick_rule: None = None
    rule_source: str = ""
    verified_on: str = ""


class SearchResult(Model):
    """搜索覆盖受供应商限制，不能解释为完整全球清单。"""

    items: list[Instrument | ObservationInstrument]
    source: str = "tencent-smartbox / eastmoney-us-classification / hkex-securities-list"
    message: str = (
        "供应商搜索结果可能截断，非完整市场清单；筛选无结果时请输入更精确代码；核验前仅供观察；可在详情申请启用"
    )


def observation_identity(value: str | InstrumentRule | ObservationInstrument) -> InstrumentRule | ObservationInstrument:
    """只供报价及日线复用身份映射，交易解析仍使用 resolve_rule。"""
    if isinstance(value, ObservationInstrument):
        return value
    return resolve_rule(value)


def merge_search(
    query: str, catalog: tuple[Instrument, ...], remote: tuple[ObservationInstrument, ...]
) -> SearchResult:
    """本地已核验名称也可搜索；调用方必须先完成远端请求，不能掩盖故障。"""
    normalized = query.strip().lower()
    known = {symbol_identity(item.symbol): item for item in catalog}
    merged: dict[str, Instrument | ObservationInstrument] = {
        item.symbol: item for item in catalog if normalized and normalized in (item.symbol + item.name).lower()
    }
    for item in remote:
        preferred = known.get(symbol_identity(item.symbol), item)
        merged[preferred.symbol] = preferred
    return SearchResult(items=list(merged.values()))


def symbol_identity(symbol: str) -> str:
    """港股前导零仅为供应商格式，保留本地账本代码而统一比较身份。"""
    code, separator, market = symbol.rpartition(".")
    if separator and market == "HK" and code.isascii() and code.isdigit():
        return f"{int(code)}.HK"
    return symbol
