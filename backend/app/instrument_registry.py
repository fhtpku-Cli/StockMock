"""加载显式证券规则，拒绝冲突和不受支持的交易类别。"""

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Final, assert_never

from pydantic import TypeAdapter, ValidationError

from app.instrument_rules import CATALOG_RULES, InstrumentRule
from app.marketdata_models import MarketDataError

_CN_BOARD_LOT: Final = 100
_CN_CODE_LENGTH: Final = 6
_HK_CODE_LENGTHS: Final = {4, 5}


def _provider_code(rule: InstrumentRule) -> str:
    match rule.market:
        case "CN":
            code, _, exchange = rule.symbol.partition(".")
            if len(code) != _CN_CODE_LENGTH or not code.isdecimal() or exchange not in {"SS", "SZ"}:
                raise MarketDataError("invalid_instrument_rule", rule.symbol)
            return ("sh" if exchange == "SS" else "sz") + code
        case "HK":
            code = rule.symbol.removesuffix(".HK")
            if not rule.symbol.endswith(".HK") or not code.isdecimal() or len(code) not in _HK_CODE_LENGTHS:
                raise MarketDataError("invalid_instrument_rule", rule.symbol)
            return "hk" + code.zfill(5)
        case "US":
            if not rule.symbol.isalpha():
                raise MarketDataError("unsupported_instrument_class", rule.symbol)
            return "us" + rule.symbol
        case _:
            assert_never(rule.market)


def validate_rule(rule: InstrumentRule) -> InstrumentRule:
    """校验规则内部一致性；数据来源和特定证券状态仍须真实核验。"""
    expected_currency = {"CN": "CNY", "HK": "HKD", "US": "USD"}[rule.market]
    if rule.currency != expected_currency or rule.provider_code != _provider_code(rule):
        raise MarketDataError("invalid_instrument_rule", rule.symbol)
    if rule.name.upper().startswith(("ST", "*ST", "S*ST")) or any(
        marker in rule.name.upper() for marker in ("退市", "退", "2X", "3X", "反向", "杠杆")
    ):
        raise MarketDataError("unsupported_instrument_class", rule.symbol)
    if rule.market == "CN":
        supported_stock = rule.provider_code.startswith(
            ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")
        )
        if rule.price_limit_ratio is None or (rule.kind == "stock" and not supported_stock):
            raise MarketDataError("unsupported_instrument_class", rule.symbol)
        if rule.kind == "stock" and rule.price_limit_ratio != Decimal(".1"):
            raise MarketDataError("invalid_instrument_rule", rule.symbol)
        if rule.settlement_days != 1 or rule.lot_size != _CN_BOARD_LOT:
            raise MarketDataError("invalid_instrument_rule", rule.symbol)
        expected_tick = Decimal(".01") if rule.kind == "stock" else Decimal(".001")
        if rule.tick_rule != "fixed" or rule.tick_size != expected_tick:
            raise MarketDataError("invalid_instrument_rule", rule.symbol)
    if rule.market == "US" and (rule.lot_size != 1 or rule.tick_rule != "US" or rule.settlement_days != 0):
        raise MarketDataError("invalid_instrument_rule", rule.symbol)
    if rule.market == "HK" and (rule.tick_rule != "HK" or rule.settlement_days != 0):
        raise MarketDataError("invalid_instrument_rule", rule.symbol)
    return rule


@lru_cache(maxsize=1)
def catalog_rules() -> tuple[InstrumentRule, ...]:
    """启动时读取已核验的本地扩展文件；缺损文件显式失败。"""
    path = os.environ.get("STOCKMOCK_INSTRUMENTS_FILE")
    if not path:
        return CATALOG_RULES
    try:
        additions = TypeAdapter(tuple[InstrumentRule, ...]).validate_json(Path(path).read_bytes())
    except (OSError, ValidationError) as exc:
        raise MarketDataError("invalid_instrument_catalog", "本地证券规则文件不可读取或格式错误") from exc
    symbols: set[str] = set()
    merged = {row.symbol: row for row in CATALOG_RULES}
    providers = {row.provider_code: row.symbol for row in CATALOG_RULES}
    for rule in additions:
        _ = validate_rule(rule)
        if rule.symbol in symbols:
            raise MarketDataError("duplicate_instrument", rule.symbol)
        if rule.provider_code in providers and providers[rule.provider_code] != rule.symbol:
            raise MarketDataError("duplicate_provider_alias", rule.symbol)
        symbols.add(rule.symbol)
        providers[rule.provider_code] = rule.symbol
        merged[rule.symbol] = rule
    return tuple(merged.values())


def resolve_rule(value: str | InstrumentRule) -> InstrumentRule:
    """解析已注册代码，导入验证可显式传入待核验规则。"""
    match value:
        case str():
            for rule in catalog_rules():
                if rule.symbol == value:
                    return rule
            raise MarketDataError("unsupported_symbol", value)
        case InstrumentRule():
            return validate_rule(value)
        case _:
            assert_never(value)
