"""缓存和分派真实行情请求；失败不会生成替代报价。"""

from __future__ import annotations

import logging
import socket
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, ClassVar, Final, assert_never

import httpx2

from app.discovery_models import ObservationInstrument, observation_identity
from app.instrument_registry import resolve_rule
from app.marketdata_daily import SDK_URL, decode_daily, parse_daily
from app.marketdata_futu import FutuOpenD
from app.marketdata_futu_parser import futu_port, parse_futu_intraday
from app.marketdata_models import Bar, Currency, FxRate, MarketDataError, Quote
from app.marketdata_parsers import instrument_config, parse_fx, parse_history, parse_intraday, parse_quote
from app.marketdata_urls import tencent_history_url
from app.marketdata_us import parse_us_intraday

if TYPE_CHECKING:
    from _thread import LockType

    from app.instrument_rules import InstrumentRule

__all__ = [
    "Bar",
    "Currency",
    "FxRate",
    "MarketDataError",
    "Quote",
    "parse_fx",
    "parse_history",
    "parse_intraday",
    "parse_quote",
]
_MAX_CACHE_ENTRIES: Final = 256


def _log_response(response: httpx2.Response) -> None:
    logging.getLogger(__name__).debug(
        "market_data_response host=%s status=%d", response.request.url.host, response.status_code
    )


class MarketDataClient:
    """维护线程安全响应缓存，合并并发请求并保留最初取得时间。"""

    response_limit: ClassVar[int] = 1_000_000

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
                if len(self._cache) >= _MAX_CACHE_ENTRIES:
                    del self._cache[next(iter(self._cache))]
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
        if url.startswith("futu:"):
            return FutuOpenD(futu_port()).history_bytes(url.removeprefix("futu:"))
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
                    if len(chunks) > self.response_limit:
                        raise MarketDataError("response_too_large", f"limit={self.response_limit}")
                return bytes(chunks)
        except httpx2.HTTPStatusError as exc:
            raise MarketDataError("http_error", str(exc.response.status_code)) from exc
        except httpx2.RequestError as exc:
            raise MarketDataError("network_error", type(exc).__name__) from exc

    def quote(self, symbol: str | InstrumentRule | ObservationInstrument) -> Quote:
        """获取只供观察和估值的真实快照。"""
        code, _, _, _ = instrument_config(symbol)
        raw, retrieved_at = self._load(f"https://qt.gtimg.cn/q={code}", 60)
        return parse_quote(raw, symbol, retrieved_at)

    def intraday(self, symbol: str | InstrumentRule) -> tuple[Quote, ...]:
        """按已核验市场分派完整区间，交易层另查会话与委托时点。"""
        rule = resolve_rule(symbol)
        match rule.market:
            case "CN":
                endpoint = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
                url = (
                    f"{endpoint}CN_MarketDataService.getKLineData?symbol={rule.provider_code}&scale=5&ma=no&datalen=240"
                )
                parser = parse_intraday
            case "US":
                endpoint = "https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/var%20_data=/"
                url = f"{endpoint}US_MinKService.getMinK?symbol={rule.symbol}&type=5&___qn=3"
                parser = parse_us_intraday
            case "HK":
                url = f"futu:{rule.provider_code[2:]}"
                parser = parse_futu_intraday
            case _:
                assert_never(rule.market)
        raw, retrieved_at = self._load(url, 60)
        return parser(raw, rule, retrieved_at)

    def history(self, symbol: str | InstrumentRule | ObservationInstrument) -> tuple[Bar, ...]:
        """美股最多 252 条、其余最多 30 条原始日线；实际数量由来源决定。"""
        rule = observation_identity(symbol)
        if rule.market == "US":
            sdk, _ = self._load(SDK_URL, 86400)
            url = f"https://finance.sina.com.cn/us_stock/company/hisdata/klc_kl_{rule.symbol}.js"
            raw, retrieved_at = self._load(url, 3600)
            return parse_daily(decode_daily(raw, sdk, rule.symbol), rule, retrieved_at)
        url = tencent_history_url(rule, 30)
        raw, retrieved_at = self._load(url, 3600)
        return parse_history(raw, symbol, retrieved_at)

    def fx_rates(self) -> tuple[FxRate, ...]:
        """取得有日期的 CNY/HKD/USD 日频参考汇率。"""
        raw, retrieved_at = self._load("https://api.frankfurter.dev/v2/rates?base=CNY&quotes=USD,HKD", 3600)
        return parse_fx(raw, retrieved_at)
