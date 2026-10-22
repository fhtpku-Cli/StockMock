"""启用交易界面验收：真实接口/核验/SQLite，只有外部数据和时钟受控。"""

import os
import tempfile
from contextlib import ExitStack
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import uvicorn
from controlled_server import TEST_NOW, QUOTE_TIME, PremarketClock
from app.admission_sources import AdmissionSources
from app.activated_registry import RuleProvider
from app.discovery import Discovery
from app.discovery_models import ObservationInstrument
from app.engine import Engine
from app.main import create_app
from app.marketdata import MarketDataClient
from app.marketdata_models import Bar, FxRate, Quote
from app.storage import Store

ITEMS = tuple(ObservationInstrument(
    symbol=symbol, name=name, market=market, currency=currency, kind=kind, provider_code=code,
) for symbol, name, market, currency, kind, code in (
    ("688981.SS", "中芯国际", "CN", "CNY", "stock", "sh688981"),
    ("603019.SS", "中科曙光", "CN", "CNY", "stock", "sh603019"),
    ("IBM", "International Business Machines", "US", "USD", "stock", "usIBM"),
    ("VUG", "Vanguard Growth ETF", "US", "USD", "ETF", "usVUG"),
    ("512660.SS", "军工ETF", "CN", "CNY", "ETF", "sh512660"),
    ("00981.HK", "中芯国际", "HK", "HKD", "stock", "hk00981"),
))


def search(_self, query):
    return tuple(item for item in ITEMS if query.lower() in (item.symbol + item.name).lower())


def price_limit_payload(_self, url, ttl):
    code = url.split("q=")[-1]
    fields = [""] * 52
    fields[4] = "100"
    fields[47], fields[48] = ("120", "80") if code.startswith("sh688") else ("110", "90")
    return (f'v_{code}="' + "~".join(fields) + '";').encode(), TEST_NOW


def quote(_self, item):
    return Quote(item.symbol, Decimal("100"), item.currency, QUOTE_TIME, TEST_NOW,
                 "受控测试行情", True, QUOTE_TIME - timedelta(minutes=5), Decimal("1000"))


def history(self, item):
    return tuple(Bar(item.symbol, day, Decimal("100"), Decimal("100"), Decimal("100"),
                     Decimal("100"), Decimal(1000 + offset), item.currency,
                     QUOTE_TIME, TEST_NOW, "受控测试日线")
                 for offset, day in enumerate((
                     (TEST_NOW - timedelta(days=150)).date(),
                     (TEST_NOW - timedelta(days=2)).date(), QUOTE_TIME.date(),
                 )))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="stockmock-tradability-ui-") as temp, ExitStack() as stack:
        for module in ("app.trading_routes", "app.asset_routes", "app.record_routes", "app.storage",
                       "app.auth", "app.admission_sources", "app.instrument_import"):
            stack.enter_context(patch(f"{module}.datetime", PremarketClock))
        stack.enter_context(patch.object(Discovery, "search", search))
        stack.enter_context(patch.object(MarketDataClient, "quote", quote))
        stack.enter_context(patch.object(MarketDataClient, "history", history))
        stack.enter_context(patch.object(AdmissionSources, "maturity_history", history))
        stack.enter_context(patch.object(AdmissionSources, "_load", price_limit_payload))
        stack.enter_context(patch.object(MarketDataClient, "intraday", lambda self, item: (quote(self, item),)))
        stack.enter_context(patch.object(MarketDataClient, "fx_rates", lambda self: (
            FxRate("USD", Decimal("7"), TEST_NOW, TEST_NOW, "受控测试汇率"),
            FxRate("HKD", Decimal(".9"), TEST_NOW, TEST_NOW, "受控测试汇率"),
        )))
        client = MarketDataClient()
        engine = Engine(Store(Path(temp) / "test.sqlite3"), client)
        engine.provider = RuleProvider(engine.registry, client)
        app = create_app(engine=engine, password=os.environ["TRADABILITY_TEST_PASSWORD"], run_worker=False)
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ["TRADABILITY_TEST_PORT"]), log_level="warning")
