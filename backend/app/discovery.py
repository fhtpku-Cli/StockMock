"""按需查询公开证券搜索；仅自选元数据持久化，不改变交易白名单。"""

import re
import sqlite3
from typing import ClassVar, Final
from urllib.parse import urlencode

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.discovery_hk import HK_LIST_URL, hk_categories
from app.discovery_models import ObservationInstrument
from app.marketdata import MarketDataClient
from app.marketdata_models import MarketDataError
from app.models import DomainError, Instrument
from app.storage import Store, read_rows

_FIELD_COUNT: Final = 5
_MAX_QUERY: Final = 80
_SPACE_CODE: Final = 32


def persist_observation(connection: sqlite3.Connection, item: Instrument | ObservationInstrument) -> None:
    """与自选写入共用事务，仅持久化已核对的观察身份。"""
    if isinstance(item, ObservationInstrument):
        _ = connection.execute(
            "INSERT INTO observations VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET data=excluded.data",
            (item.symbol, item.model_dump_json()),
        )


def parse_search(raw: bytes) -> tuple[ObservationInstrument, ...]:
    """白名单接受股票及 ETF，忽略债券、期货、基金份额、权证和未知类别。"""
    try:
        text = raw.decode("gb18030").strip().removesuffix(";")
        if not text.startswith("v_hint="):
            raise MarketDataError("invalid_search_response", "搜索响应格式改变")
        payload = TypeAdapter(str).validate_json(text.removeprefix("v_hint="))
    except (UnicodeError, ValueError) as exc:
        raise MarketDataError("invalid_search_response", "搜索响应无法解析") from exc
    items: dict[str, ObservationInstrument] = {}
    for row in payload.split("^"):
        fields = row.split("~")
        if len(fields) != _FIELD_COUNT:
            continue
        exchange, code, name, _, kind = fields
        if kind not in {"GP-A", "GP-A-KCB", "GP", "ETF"} or not name.strip():
            continue
        item = _item(exchange, code, name, kind)
        if item is not None:
            items[item.symbol] = item
    return tuple(items.values())


def _item(exchange: str, code: str, name: str, kind: str) -> ObservationInstrument | None:
    if exchange in {"sh", "sz", "bj"} and re.fullmatch(r"[0-9]{6}", code) and kind in {"GP-A", "GP-A-KCB", "ETF"}:
        suffix = {"sh": "SS", "sz": "SZ", "bj": "BJ"}[exchange]
        return ObservationInstrument(
            symbol=f"{code}.{suffix}",
            name=name,
            market="CN",
            currency="CNY",
            kind="ETF" if kind == "ETF" else "stock",
            provider_code=exchange + code,
        )
    if exchange == "hk" and re.fullmatch(r"[0-9]{5}", code) and kind in {"GP", "ETF"}:
        return ObservationInstrument(
            symbol=f"{code}.HK",
            name=name,
            market="HK",
            currency="HKD",
            kind="ETF" if kind == "ETF" else "stock",
            provider_code="hk" + code,
        )
    if exchange == "us" and kind in {"GP", "ETF"}:
        ticker = re.sub(r"\.(oq|n|am|a)$", "", code.lower()).upper()
        if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker):
            return ObservationInstrument(
                symbol=ticker,
                name=name,
                market="US",
                currency="USD",
                kind="ETF" if kind == "ETF" else "stock",
                provider_code="us" + ticker,
            )
    return None


class Discovery(MarketDataClient):
    """复用有界请求缓存与行情解析，搜索不会为结果逐一请求报价。"""

    response_limit: ClassVar[int] = 2_000_000

    def search(self, query: str) -> tuple[ObservationInstrument, ...]:
        """用户文本统一编码为 URL 参数，每次最多两个搜索请求及一份有日频缓存的港交所清单，不逐股抓取行情。"""
        query = query.strip()
        if not query:
            return ()
        if len(query) > _MAX_QUERY or any(ord(char) < _SPACE_CODE for char in query):
            raise DomainError("invalid_search_query", "请输入 1 至 80 个可见字符", 422)
        url = "https://smartbox.gtimg.cn/s3/?" + urlencode({"q": query, "t": "all"})
        raw, _ = self._load(url, 300)
        items = parse_search(raw)
        result = [item for item in items if item.market == "CN"]
        if any(item.market == "US" for item in items):
            url = "https://searchapi.eastmoney.com/api/suggest/get?" + urlencode(
                {"input": query, "type": "14", "count": "20"}
            )
            classification_raw, _ = self._load(url, 300)
            symbols = {item.symbol for item in items if item.market == "US"}
            result.extend(item for item in parse_us_search(classification_raw) if item.symbol in symbols)
        if any(item.market == "HK" for item in items):
            catalog_raw, _ = self._load(HK_LIST_URL, 86400)
            categories = hk_categories(catalog_raw)
            result.extend(
                item.model_copy(update={"kind": categories[item.provider_code[2:]]})
                for item in items
                if item.market == "HK" and item.provider_code[2:] in categories
            )
        return tuple(result)

    def lookup(self, symbol: str, store: Store) -> ObservationInstrument:
        """先读持久自选身份，否则通过实时搜索核对代码，拒绝任意输入。"""
        with store.transaction() as connection:
            rows = read_rows(connection, "SELECT data FROM observations WHERE symbol=?", (symbol,))
        if rows:
            return ObservationInstrument.model_validate_json(rows[0][0])
        query = symbol.rsplit(".", 1)[0] if symbol.endswith((".SS", ".SZ", ".HK", ".BJ")) else symbol
        for item in self.search(query):
            if item.symbol == symbol:
                return item
        raise DomainError("unknown_instrument", "未找到可观察证券，请重新搜索后添加", 404)


class _UsResult(BaseModel):
    code: str = Field(alias="Code")
    name: str = Field(alias="Name")
    classification: str = Field(alias="Classify")
    security_type: str = Field(alias="SecurityType")
    type_us: str = Field(alias="TypeUS")
    exchange: str = Field(alias="JYS")


class _SearchTable(BaseModel):
    rows: list[_UsResult] | None = Field(alias="Data")
    status: int = Field(alias="Status")


class _SearchEnvelope(BaseModel):
    table: _SearchTable = Field(alias="QuotationCodeTable")


def parse_us_search(raw: bytes) -> tuple[ObservationInstrument, ...]:
    """腾讯 GP 不能区分美股 ETF，因此用东方财富明确类别确认美股身份。"""
    try:
        payload = _SearchEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        raise MarketDataError("invalid_search_response", "美股类别确认失败") from exc
    if payload.table.status != 0:
        raise MarketDataError("search_unavailable", "美股搜索暂不可用")
    return tuple(
        ObservationInstrument(
            symbol=row.code.upper(),
            name=row.name,
            market="US",
            currency="USD",
            kind="ETF" if row.type_us == "5" else "stock",
            provider_code="us" + row.code.upper(),
        )
        for row in payload.table.rows or []
        if row.classification == "UsStock"
        and row.security_type in {"7", "20"}
        and row.type_us in {"1", "5"}
        and row.exchange in {"NASDAQ", "NYSE", "AMEX"}
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,14}", row.code)
    )
