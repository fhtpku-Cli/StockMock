"""个人研究行情边界：腾讯估值、新浪完整分钟线、Frankfurter 日频汇率。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from app.discovery_models import ObservationInstrument, observation_identity
from app.instrument_registry import resolve_rule
from app.marketdata_models import Bar, Currency, FxRate, MarketDataError, Quote

if TYPE_CHECKING:
    from app.instrument_rules import InstrumentRule

__all__ = ["Bar", "Currency", "FxRate", "MarketDataError", "Quote"]

SOURCE: Final = "tencent-public"
_FX_CURRENCIES: Final = {"HKD", "USD"}


class _Boundary(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, allow_inf_nan=False)


class _Daily(_Boundary):
    day: list[list[JsonValue]] = Field(default_factory=list)


class _History(_Boundary):
    code: int
    data: dict[str, _Daily]


class _Minute(_Boundary):
    day: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)


class _Fx(_Boundary):
    date: date
    base: Literal["CNY"]
    quote: Literal["HKD", "USD"]
    rate: Decimal = Field(gt=0)


def instrument_config(symbol: str | InstrumentRule | ObservationInstrument) -> tuple[str, Currency, ZoneInfo, str]:
    """时区由已核验市场定义，证券映射由显式目录定义。"""
    rule = observation_identity(symbol)
    zone, pattern = {
        "CN": ("Asia/Shanghai", "%Y%m%d%H%M%S"),
        "HK": ("Asia/Hong_Kong", "%Y/%m/%d %H:%M:%S"),
        "US": ("America/New_York", "%Y-%m-%d %H:%M:%S"),
    }[rule.market]
    return rule.provider_code, rule.currency, ZoneInfo(zone), pattern


def parse_quote(raw: bytes, symbol: str | InstrumentRule | ObservationInstrument, retrieved_at: datetime) -> Quote:
    """腾讯字段 30 仅是供应商时间，始终不可作为成交依据。"""
    code, currency, zone, pattern = instrument_config(symbol)
    symbol = observation_identity(symbol).symbol
    try:
        text = raw.decode("gb18030").strip()
        prefix = f'v_{code}="'
        if not text.startswith(prefix) or not text.endswith('";'):
            raise MarketDataError("invalid_response", symbol)
        fields = text[len(prefix) : -2].split("~")
        if any(marker in fields[1].upper() for marker in ("ST", "退")) and currency == "CNY":
            raise MarketDataError("unsupported_instrument_status", symbol)
        price = TypeAdapter(Decimal).validate_python(fields[3])
        stamp = datetime.strptime(fields[30], pattern).replace(tzinfo=zone).astimezone(UTC)
        if not price.is_finite() or price <= 0 or stamp > retrieved_at + timedelta(minutes=1):
            raise MarketDataError("invalid_quote", symbol)
        return Quote(symbol, price, currency, stamp, retrieved_at, SOURCE)
    except (UnicodeError, IndexError, ValueError) as exc:
        raise MarketDataError("invalid_response", symbol) from exc


def parse_intraday(raw: bytes, symbol: str | InstrumentRule, retrieved_at: datetime) -> tuple[Quote, ...]:
    """新浪五分钟时间标签为区间末端；只输出原始取得时已结束且有成交量的区间。"""
    _, currency, zone, _ = instrument_config(symbol)
    symbol = resolve_rule(symbol).symbol
    if currency != "CNY":
        raise MarketDataError("intraday_unverified", symbol)
    try:
        text = raw.decode("utf-8")
        start, end = text.index("([") + 1, text.rindex("]);") + 1
        rows = TypeAdapter(list[_Minute]).validate_json(text[start:end])
        results: list[Quote] = []
        seen: set[datetime] = set()
        for row in rows:
            valid_range = row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high
            if row.day.tzinfo is not None or not valid_range:
                raise MarketDataError("invalid_bar", symbol)
            stamp = row.day.replace(tzinfo=zone).astimezone(UTC)
            if row.day.minute % 5 or row.day.second or row.day.microsecond:
                raise MarketDataError("invalid_bar_time", symbol)
            if stamp in seen:
                raise MarketDataError("duplicate_bar", symbol)
            seen.add(stamp)
            if stamp <= retrieved_at and row.volume > 0:
                results.append(
                    Quote(
                        symbol,
                        row.close,
                        currency,
                        stamp,
                        retrieved_at,
                        "sina-public-5m",
                        tradable=True,
                        interval_start=stamp - timedelta(minutes=5),
                        volume=row.volume,
                    )
                )
        return tuple(sorted(results, key=lambda row: row.market_time))
    except (UnicodeError, ValueError) as exc:
        raise MarketDataError("invalid_response", symbol) from exc


def parse_history(
    raw: bytes, symbol: str | InstrumentRule | ObservationInstrument, retrieved_at: datetime
) -> tuple[Bar, ...]:
    """日线日期仅作图表标签；不推导具体成交时点，不用于撮合。"""
    code, currency, zone, _ = instrument_config(symbol)
    symbol = observation_identity(symbol).symbol
    try:
        payload = _History.model_validate_json(raw)
        if payload.code != 0 or code not in payload.data or not payload.data[code].day:
            raise MarketDataError("history_unavailable", symbol)
        bars: list[Bar] = []
        for row in payload.data[code].day:
            day, opening, close, high, low, volume = TypeAdapter(
                tuple[date, Decimal, Decimal, Decimal, Decimal, Decimal],
            ).validate_python(row[:6])
            finite = all(value.is_finite() for value in (opening, close, high, low, volume))
            if not finite or not 0 < low <= min(opening, close) <= max(opening, close) <= high or volume < 0:
                raise MarketDataError("invalid_bar", symbol)
            bars.append(
                Bar(
                    symbol,
                    day,
                    opening,
                    high,
                    low,
                    close,
                    volume,
                    currency,
                    datetime.combine(day, time.min, zone).astimezone(UTC),
                    retrieved_at,
                    SOURCE,
                )
            )
        return tuple(bars)
    except ValidationError as exc:
        raise MarketDataError("invalid_response", symbol) from exc


def parse_fx(raw: bytes, retrieved_at: datetime) -> tuple[FxRate, ...]:
    """以每日参考值倒数得到每单位外币对应人民币，不伪造发布时间。"""
    try:
        rows = TypeAdapter(list[_Fx]).validate_json(raw)
        if len(rows) != len(_FX_CURRENCIES) or {row.quote for row in rows} != _FX_CURRENCIES:
            raise MarketDataError("incomplete_fx", "HKD/USD")
        if len({row.date for row in rows}) != 1 or any(row.date > retrieved_at.date() for row in rows):
            raise MarketDataError("invalid_fx_date", "HKD/USD")
        rates = tuple(
            FxRate(
                row.quote,
                Decimal(1) / row.rate,
                datetime.combine(row.date, time.min, UTC),
                retrieved_at,
                "frankfurter-v2-daily",
            )
            for row in rows
        )
        return (FxRate("CNY", Decimal(1), rates[0].market_time, retrieved_at, "currency-identity"), *rates)
    except ValidationError as exc:
        raise MarketDataError("invalid_response", "FX") from exc
