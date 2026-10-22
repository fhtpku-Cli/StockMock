"""在无宿主能力的受限解释器中读取新浪官方编码日线，不分发供应商脚本。"""

import json
import re
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import ClassVar, Final
from zoneinfo import ZoneInfo

import quickjs
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.discovery_models import ObservationInstrument, observation_identity
from app.instrument_rules import InstrumentRule
from app.marketdata_models import Bar, MarketDataError

SDK_URL: Final = "https://finance.sina.com.cn/sinafinancesdk/js/sf_sdk.js"
_MAX_INPUT: Final = 1_000_000
_MAX_OUTPUT: Final = 1_000_000
_MARKER: Final = "this.xh5_S_KLC_D="
_ENDING: Final = ";return v()()};var H="


def extract_decoder(sdk: bytes) -> str:
    """仅提取已观察到的独立解码函数；结构变化显式失败，不执行整个 SDK。"""
    if len(sdk) > _MAX_INPUT:
        raise MarketDataError("decoder_source_changed", "SDK 超出上限")
    try:
        source = sdk.decode("utf-8")
        if source.count(_MARKER) != 1 or source.count(_ENDING) != 1:
            raise MarketDataError("decoder_source_changed", "官方解码函数结构已改变")
        start = source.index(_MARKER) + len(_MARKER)
        end = source.index(_ENDING, start) + len(";return v()()}")
        function = source[start:end]
        if not function.startswith("function(t){"):
            raise MarketDataError("decoder_source_changed", "解码函数签名已改变")
    except (UnicodeError, ValueError) as exc:
        raise MarketDataError("decoder_source_changed", "SDK 不是预期文本") from exc
    return function


def decode_daily(raw: bytes, sdk: bytes, symbol: str) -> str:
    """隔离上下文限制 CPU、内存和栈；不注册网络、文件或 Python 回调。"""
    if len(raw) > _MAX_INPUT:
        raise MarketDataError("invalid_daily_encoding", "日线响应超出上限")
    pattern = rb"\s*var KLC_KL_" + re.escape(symbol.encode()) + rb'="([A-Za-z0-9+/]+)"\s*;'
    match = re.match(pattern, raw)
    if match is None:
        raise MarketDataError("invalid_daily_encoding", symbol)
    encoded = match[1].decode("ascii")
    function = extract_decoder(sdk)
    context = quickjs.Context()
    context.set_memory_limit(32 * 1024 * 1024)
    context.set_max_stack_size(512 * 1024)
    context.set_time_limit(0.5)
    script = f"JSON.stringify(({function})({json.dumps(encoded)}).slice(-252))"
    try:
        result = context.eval(script)
    except quickjs.JSException as exc:
        raise MarketDataError("daily_decode_failed", "解码格式变化或超出运行资源限制") from exc
    if not isinstance(result, str) or len(result) > _MAX_OUTPUT:
        raise MarketDataError("invalid_daily_decoded", "解码结果不是有界 JSON")
    return result


class _DailyRow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, allow_inf_nan=False)
    date: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)


def parse_daily(
    decoded: str, symbol: str | InstrumentRule | ObservationInstrument, retrieved_at: datetime
) -> tuple[Bar, ...]:
    """只保留已收市的日线；日期标签不作为撮合时间。"""
    rule = observation_identity(symbol)
    if rule.market != "US":
        raise MarketDataError("unsupported_market", rule.symbol)
    try:
        rows = TypeAdapter(tuple[_DailyRow, ...]).validate_json(decoded)
    except ValidationError as exc:
        raise MarketDataError("invalid_daily_decoded", rule.symbol) from exc
    bars: list[Bar] = []
    seen: set[datetime] = set()
    for row in rows:
        if row.date.tzinfo is None or row.date.utcoffset() != UTC.utcoffset(None) or row.date.time() != time.min:
            raise MarketDataError("invalid_daily_date", rule.symbol)
        if row.date in seen or not row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high:
            raise MarketDataError("invalid_daily_bar", rule.symbol)
        seen.add(row.date)
        day = row.date.date()
        close_time = datetime.combine(day, time(16), ZoneInfo("America/New_York"))
        if close_time <= retrieved_at:
            bars.append(
                Bar(
                    rule.symbol,
                    day,
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    row.volume,
                    "USD",
                    row.date,
                    retrieved_at,
                    "sina-us-daily-raw",
                )
            )
    if not bars:
        raise MarketDataError("empty_history", rule.symbol)
    return tuple(sorted(bars, key=lambda row: row.day))
