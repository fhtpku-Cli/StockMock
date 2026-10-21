"""个人研究行情边界：腾讯估值、新浪完整分钟线、Frankfurter 日频汇率。"""

from __future__ import annotations

import logging
import socket
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, ClassVar, Final, Literal, assert_never
from zoneinfo import ZoneInfo

import httpx2
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from app.marketdata_models import Bar, Currency, FxRate, MarketDataError, Quote

if TYPE_CHECKING:
    from _thread import LockType

__all__ = ["Bar", "Currency", "FxRate", "MarketDataError", "Quote"]

SOURCE: Final = "tencent-public"
_MAX_RESPONSE_BYTES: Final = 1_000_000
_FX_CURRENCIES: Final = {"HKD", "USD"}
_SYMBOLS: Final = {
    "600519.SS": ("sh600519", "CNY", "Asia/Shanghai", "%Y%m%d%H%M%S"),
    "510300.SS": ("sh510300", "CNY", "Asia/Shanghai", "%Y%m%d%H%M%S"),
    "000001.SZ": ("sz000001", "CNY", "Asia/Shanghai", "%Y%m%d%H%M%S"),
    "159915.SZ": ("sz159915", "CNY", "Asia/Shanghai", "%Y%m%d%H%M%S"),
    "0700.HK": ("hk00700", "HKD", "Asia/Hong_Kong", "%Y/%m/%d %H:%M:%S"),
    "2800.HK": ("hk02800", "HKD", "Asia/Hong_Kong", "%Y/%m/%d %H:%M:%S"),
    "AAPL": ("usAAPL", "USD", "America/New_York", "%Y-%m-%d %H:%M:%S"),
    "SPY": ("usSPY", "USD", "America/New_York", "%Y-%m-%d %H:%M:%S"),
}


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


def _instrument(symbol: str) -> tuple[str, Currency, ZoneInfo, str]:
    if symbol not in _SYMBOLS:
        raise MarketDataError("unsupported_symbol", symbol)
    code, currency, zone, pattern = _SYMBOLS[symbol]
    return code, TypeAdapter(Currency).validate_python(currency), ZoneInfo(zone), pattern


def parse_quote(raw: bytes, symbol: str, retrieved_at: datetime) -> Quote:
    """腾讯字段 30 仅是供应商时间，始终不可作为成交依据。"""
    code, currency, zone, pattern = _instrument(symbol)
    try:
        text = raw.decode("gb18030").strip()
        prefix = f'v_{code}="'
        if not text.startswith(prefix) or not text.endswith('";'):
            raise MarketDataError("invalid_response", symbol)
        fields = text[len(prefix) : -2].split("~")
        price = TypeAdapter(Decimal).validate_python(fields[3])
        stamp = datetime.strptime(fields[30], pattern).replace(tzinfo=zone).astimezone(UTC)
        if not price.is_finite() or price <= 0 or stamp > retrieved_at + timedelta(minutes=1):
            raise MarketDataError("invalid_quote", symbol)
        return Quote(symbol, price, currency, stamp, retrieved_at, SOURCE)
    except (UnicodeError, IndexError, ValueError) as exc:
        raise MarketDataError("invalid_response", symbol) from exc


def parse_intraday(raw: bytes, symbol: str, retrieved_at: datetime) -> tuple[Quote, ...]:
    """新浪五分钟时间标签为区间末端；只输出已结束且有成交量的区间。"""
    _, currency, zone, _ = _instrument(symbol)
    if currency != "CNY":
        raise MarketDataError("intraday_unverified", symbol)
    try:
        text = raw.decode("utf-8")
        start, end = text.index("([") + 1, text.rindex("]);") + 1
        rows = TypeAdapter(list[_Minute]).validate_json(text[start:end])
        results: list[Quote] = []
        for row in rows:
            valid_range = row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high
            if row.day.tzinfo is not None or not valid_range:
                raise MarketDataError("invalid_bar", symbol)
            stamp = row.day.replace(tzinfo=zone).astimezone(UTC)
            if row.day.minute % 5 or row.day.second:
                raise MarketDataError("invalid_bar_time", symbol)
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
        if len({row.market_time for row in results}) != len(results):
            raise MarketDataError("duplicate_bar", symbol)
        return tuple(sorted(results, key=lambda row: row.market_time))
    except (UnicodeError, ValueError) as exc:
        raise MarketDataError("invalid_response", symbol) from exc


def parse_history(raw: bytes, symbol: str, retrieved_at: datetime) -> tuple[Bar, ...]:
    """日线日期仅作图表标签；不推导具体成交时点，不用于撮合。"""
    code, currency, zone, _ = _instrument(symbol)
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


def _log_response(response: httpx2.Response) -> None:
    logging.getLogger(__name__).debug(
        "market_data_response host=%s status=%d", response.request.url.host, response.status_code
    )


class MarketDataClient:
    """维护线程安全响应缓存，合并并发请求并保留最初取得时间。"""

    def __init__(self) -> None:
        """每个客户端独立缓存，锁覆盖未命中请求以防并发重复访问。"""
        self._lock: LockType = Lock()
        self._cache: dict[str, tuple[float, datetime, bytes | MarketDataError]] = {}

    def _load(self, url: str, ttl: int) -> tuple[bytes, datetime]:
        with self._lock:
            entry = self._cache.get(url)
            if entry is None or entry[0] <= monotonic():
                value: bytes | MarketDataError
                duration = ttl
                try:
                    value = self._get(url)
                except MarketDataError as error:
                    value, duration = error, 15
                entry = (monotonic() + duration, datetime.now(UTC), value)
                self._cache[url] = entry
            _, retrieved_at, value = entry
            match value:
                case bytes():
                    return value, retrieved_at
                case MarketDataError():
                    raise MarketDataError(value.code, value.detail)
                case _:
                    assert_never(value)

    def _get(self, url: str) -> bytes:
        transport = httpx2.HTTPTransport(
            http2=True,
            retries=3,
            limits=httpx2.Limits(max_connections=200, max_keepalive_connections=40, keepalive_expiry=30),
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        )
        try:
            with (
                httpx2.Client(
                    transport=transport,
                    timeout=httpx2.Timeout(connect=5, read=15, write=10, pool=10),
                    follow_redirects=True,
                    event_hooks={"response": [_log_response]},
                ) as client,
                client.stream("GET", url) as response,
            ):
                _ = response.raise_for_status()
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _MAX_RESPONSE_BYTES:
                        raise MarketDataError("response_too_large", "limit=1000000")
                return bytes(chunks)
        except httpx2.HTTPStatusError as exc:
            raise MarketDataError("http_error", str(exc.response.status_code)) from exc
        except httpx2.RequestError as exc:
            raise MarketDataError("network_error", type(exc).__name__) from exc

    def quote(self, symbol: str) -> Quote:
        """获取只供观察和估值的真实快照。"""
        code, _, _, _ = _instrument(symbol)
        raw, retrieved_at = self._load(f"https://qt.gtimg.cn/q={code}", 60)
        return parse_quote(raw, symbol, retrieved_at)

    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        """获取境内证券完整五分钟候选，交易层另查合法时段。"""
        code, currency, _, _ = _instrument(symbol)
        if currency != "CNY":
            raise MarketDataError("intraday_unverified", symbol)
        endpoint = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
        url = f"{endpoint}CN_MarketDataService.getKLineData?symbol={code}&scale=5&ma=no&datalen=240"
        raw, retrieved_at = self._load(url, 60)
        return parse_intraday(raw, symbol, retrieved_at)

    def history(self, symbol: str) -> tuple[Bar, ...]:
        """读取最多三十条未复权日线，实际数量由来源决定。"""
        code, _, _, _ = _instrument(symbol)
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,30,"
        raw, retrieved_at = self._load(url, 3600)
        return parse_history(raw, symbol, retrieved_at)

    def fx_rates(self) -> tuple[FxRate, ...]:
        """取得有日期的 CNY/HKD/USD 日频参考汇率。"""
        raw, retrieved_at = self._load("https://api.frankfurter.dev/v2/rates?base=CNY&quotes=USD,HKD", 3600)
        return parse_fx(raw, retrieved_at)
