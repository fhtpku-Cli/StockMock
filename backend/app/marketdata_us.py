"""新浪官方网页 SDK 使用的美股五分钟未复权图表边界。"""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import ClassVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.instrument_registry import resolve_rule
from app.instrument_rules import InstrumentRule
from app.marketdata_models import US_SETTLE, MarketDataError, Quote, interval_is_settled


class _UsBar(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, allow_inf_nan=False)
    d: datetime
    o: Decimal = Field(gt=0)
    h: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0, alias="l")
    c: Decimal = Field(gt=0)
    v: Decimal = Field(ge=0)


def parse_us_intraday(raw: bytes, symbol: str | InstrumentRule, retrieved_at: datetime) -> tuple[Quote, ...]:
    """标签为美东区间末端；排除未定稿、零量及盘前盘后数据。"""
    rule = resolve_rule(symbol)
    if rule.market != "US":
        raise MarketDataError("unsupported_market", rule.symbol)
    try:
        text = raw.decode("utf-8")
        start, end = text.index("([") + 1, text.rindex("]);") + 1
        rows = TypeAdapter(list[_UsBar]).validate_json(text[start:end])
    except (UnicodeError, ValueError, ValidationError) as exc:
        raise MarketDataError("invalid_response", rule.symbol) from exc
    quotes: list[Quote] = []
    seen: set[datetime] = set()
    for row in rows:
        if row.d.tzinfo is not None or row.d.minute % 5 or row.d.second or row.d.microsecond:
            raise MarketDataError("invalid_bar_time", rule.symbol)
        if not row.low <= min(row.o, row.c) <= max(row.o, row.c) <= row.h or row.d in seen:
            raise MarketDataError("invalid_bar", rule.symbol)
        seen.add(row.d)
        stamp = row.d.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
        regular = row.d.weekday() in range(5) and time(9, 35) <= row.d.time() <= time(16)
        if regular and stamp <= retrieved_at and row.v > 0:
            quotes.append(
                Quote(
                    rule.symbol,
                    row.c,
                    "USD",
                    stamp,
                    retrieved_at,
                    "sina-us-5m",
                    tradable=True,
                    interval_start=stamp - timedelta(minutes=5),
                    volume=row.v,
                )
            )
    return tuple(
        row
        for row in sorted(quotes, key=lambda row: row.market_time)
        if interval_is_settled(row.market_time, retrieved_at, US_SETTLE)
    )
