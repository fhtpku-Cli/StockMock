"""OpenD 港股区间边界；明确配置标签口径后才生成可撮合候选。"""

import os
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import ClassVar, Literal, assert_never
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.instrument_registry import resolve_rule
from app.instrument_rules import InstrumentRule
from app.marketdata_futu import MAX_TCP_PORT
from app.marketdata_models import MarketDataError, Quote


class _Row(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, allow_inf_nan=False)
    time: datetime
    is_blank: bool = Field(alias="isBlank")
    high: Decimal = Field(default=Decimal(0), ge=0, alias="highPrice")
    low: Decimal = Field(default=Decimal(0), ge=0, alias="lowPrice")
    open: Decimal = Field(default=Decimal(0), ge=0, alias="openPrice")
    close: Decimal = Field(default=Decimal(0), ge=0, alias="closePrice")
    volume: Decimal = Field(default=Decimal(0), ge=0)

    def interval(self, label: Literal["start", "end"]) -> tuple[datetime, datetime]:
        """由已核验的供应商标签口径还原完整区间。"""
        if self.time.tzinfo is not None or self.time.second or self.time.minute % 5 or self.time.microsecond:
            raise MarketDataError("invalid_bar_time", "OpenD 时间未对齐五分钟")
        if not 0 < self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise MarketDataError("invalid_bar", "OpenD OHLC 不一致")
        stamp = self.time.replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
        match label:
            case "start":
                return stamp, stamp + timedelta(minutes=5)
            case "end":
                return stamp - timedelta(minutes=5), stamp
            case _:
                assert_never(label)


class _Security(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    market: Literal[1]
    code: str


class _Content(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    security: _Security
    bars: tuple[_Row, ...] = Field(default=(), alias="klList")
    next_key: str = Field(default="", alias="nextReqKey")


class _Response(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    ret_type: Literal[0] = Field(alias="retType")
    s2c: _Content


def futu_port() -> int:
    """显式配置才能接入本机 OpenD，不自动发现或读取账户信息。"""
    value = os.environ.get("STOCKMOCK_FUTU_PORT")
    if value is None:
        raise MarketDataError("futu_not_configured", "请先由本人登录本机 Futu OpenD，再配置 STOCKMOCK_FUTU_PORT")
    try:
        port = int(value)
    except ValueError as exc:
        raise MarketDataError("futu_invalid_port", "OpenD 端口必须为整数") from exc
    if not 0 < port <= MAX_TCP_PORT:
        raise MarketDataError("futu_invalid_port", "OpenD 端口超出范围")
    return port


def parse_futu_intraday(raw: bytes, symbol: str | InstrumentRule, retrieved_at: datetime) -> tuple[Quote, ...]:
    """标签口径须在真实接入验收后配置 start/end，默认不猜测。"""
    rule = resolve_rule(symbol)
    try:
        label = TypeAdapter(Literal["start", "end"]).validate_python(os.environ.get("STOCKMOCK_FUTU_BAR_LABEL"))
        payload = _Response.model_validate_json(raw).s2c
    except ValidationError as exc:
        raise MarketDataError("futu_unverified_response", "需核对 OpenD 响应和分钟标签配置") from exc
    if rule.market != "HK" or payload.security.code != rule.provider_code[2:] or payload.next_key:
        raise MarketDataError("futu_incomplete_or_mismatched", rule.symbol)
    quotes: list[Quote] = []
    seen: set[datetime] = set()
    for row in payload.bars:
        if row.time in seen:
            raise MarketDataError("duplicate_bar", rule.symbol)
        seen.add(row.time)
        if row.is_blank:
            continue
        start, end = row.interval(label)
        regular = (
            time(9, 30) <= start.time() < end.time() <= time(12) or time(13) <= start.time() < end.time() <= time(16)
        ) and start.weekday() in range(5)
        if regular and end <= retrieved_at and row.volume > 0:
            quotes.append(
                Quote(
                    rule.symbol,
                    row.close,
                    "HKD",
                    end.astimezone(UTC),
                    retrieved_at,
                    "futu-hk-5m-raw",
                    tradable=True,
                    interval_start=start.astimezone(UTC),
                    volume=row.volume,
                )
            )
    return tuple(sorted(quotes, key=lambda row: row.market_time))
