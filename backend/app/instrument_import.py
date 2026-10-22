"""用显式交易规则和真实行情核验扩展本地目录；不推断未知证券类别。"""

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.instrument_registry import validate_rule
from app.instrument_rules import CATALOG_RULES, InstrumentRule
from app.marketdata import MarketDataClient
from app.marketdata_models import MarketDataError


class ImportEntry(BaseModel):
    """本人从官方资料确认的类别和上市日期；行情请求不能代替这些事实。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    rule: InstrumentRule
    listing_date: date | None = None
    listed_on_or_before: date | None = None
    status: Literal["normal"]
    product: Literal["ordinary_stock", "unleveraged_equity_etf"]
    metadata_source: str = Field(min_length=1)

    def maturity_bound(self) -> date:
        """精确上市日或官方资料证明的最晚上市日二选一，不把年报日伪称 IPO 日。"""
        dates = tuple(value for value in (self.listing_date, self.listed_on_or_before) if value is not None)
        if len(dates) != 1:
            raise MarketDataError("invalid_listing_evidence", self.rule.symbol)
        return dates[0]


def verify_entry(entry: ImportEntry, client: MarketDataClient) -> InstrumentRule:
    """只有规则自洽且真实股票行情通过，才生成可加载目录记录。"""
    rule = validate_rule(entry.rule)
    today = datetime.now(UTC).date()
    if (
        entry.maturity_bound() > today - timedelta(days=90)
        or not today - timedelta(days=7) <= rule.verified_on <= today
    ):
        raise MarketDataError("unverified_instrument_metadata", rule.symbol)
    if (entry.product == "ordinary_stock") != (rule.kind == "stock"):
        raise MarketDataError("invalid_instrument_rule", rule.symbol)
    for source in (entry.metadata_source, rule.rule_source):
        parsed = urlsplit(source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise MarketDataError("invalid_metadata_source", rule.symbol)
    quote = client.quote(rule)
    if quote.currency != rule.currency or quote.market_time < datetime.now(UTC) - timedelta(days=7):
        raise MarketDataError("stale_or_mismatched_quote", rule.symbol)
    if rule.tradable:
        bars = client.intraday(rule)
        if not bars or bars[-1].market_time < datetime.now(UTC) - timedelta(days=7):
            raise MarketDataError("unverified_intraday", rule.symbol)
    return rule


def import_catalog(source: Path, destination: Path, client: MarketDataClient) -> int:
    """全量核验成功后才排他写入；失败不产生半份目录或覆盖旧文件。"""
    try:
        entries = TypeAdapter(tuple[ImportEntry, ...]).validate_json(source.read_bytes())
    except (OSError, ValidationError) as exc:
        raise MarketDataError("invalid_import_file", "导入文件不可读取或规则不完整") from exc
    if not entries:
        raise MarketDataError("empty_import", "至少提供一条显式规则")
    if len({entry.rule.symbol for entry in entries}) != len(entries):
        raise MarketDataError("duplicate_instrument", "导入文件证券代码重复")
    providers = {row.provider_code: row.symbol for row in CATALOG_RULES}
    for entry in entries:
        rule = validate_rule(entry.rule)
        if rule.provider_code in providers and providers[rule.provider_code] != rule.symbol:
            raise MarketDataError("duplicate_provider_alias", rule.symbol)
        providers[rule.provider_code] = rule.symbol
    if destination.exists():
        raise MarketDataError("catalog_exists", "输出目录已存在；请选择新文件并在核验后切换配置")
    rules = tuple(verify_entry(entry, client) for entry in entries)
    payload = TypeAdapter(tuple[InstrumentRule, ...]).dump_json(rules, indent=2)
    try:
        with destination.open("xb") as stream:
            _ = stream.write(payload)
    except OSError as exc:
        raise MarketDataError("catalog_write_failed", "本地目录无法写入") from exc
    return len(rules)


def main() -> int:
    """真实 CLI 入口；失败只输出错误码与可操作原因，不输出原始行情。"""
    parser = argparse.ArgumentParser(description="核验并导入本地证券目录")
    _ = parser.add_argument("source", type=Path, help="含显式交易规则及官方资料的 JSON")
    _ = parser.add_argument("destination", type=Path, help="新的本地目录 JSON；不覆盖既有文件")
    args = _Arguments.model_validate(vars(parser.parse_args()))
    try:
        count = import_catalog(args.source, args.destination, MarketDataClient())
    except MarketDataError as exc:
        _ = sys.stderr.write(f"{exc.code}: {exc.detail}\n")
        return 1
    _ = sys.stdout.write(f"已核验 {count} 条规则。配置 STOCKMOCK_INSTRUMENTS_FILE 后重启后台加载。\n")
    return 0


class _Arguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    source: Path
    destination: Path


if __name__ == "__main__":
    raise SystemExit(main())
