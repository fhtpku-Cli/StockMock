"""七项风险预览验收。仅使用候选页面、临时 SQLite 和受控行情。"""

import argparse
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from threading import Event
from unittest.mock import patch

import uvicorn
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.engine import Engine
from app.main import create_app
from app.marketdata_models import MarketDataError
from app.models import Lot
from app.storage import Store
from tests.test_engine import NOW, ControlledProvider

ROOT = Path(__file__).resolve().parents[2]


class Clock(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class Prices(ControlledProvider):
    missing: Event = field(default_factory=Event)

    def quote(self, symbol):
        if self.missing.is_set() and symbol == "600519.SS":
            raise MarketDataError("controlled_outage", "隔离验收：模拟缺少该持仓报价")
        return replace(ControlledProvider.quote(self, symbol), source="受控测试行情")

    def intraday(self, symbol):
        return (
            replace(ControlledProvider.quote(self, symbol), source="受控测试分钟线"),
        )


class FixtureHost:
    """以独立浏览器域名隔离 Cookie。仅转发此测试站点的请求到本机应用。"""

    def __init__(self, app: ASGIApp, port: int):
        self.app = app
        self.site = f"phase1.localhost:{port}".encode()
        self.local = f"127.0.0.1:{port}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if (
                headers.get(b"host") != self.site
                or headers.get(b"origin", b"http://" + self.site)
                != b"http://" + self.site
            ):
                await PlainTextResponse("仅限隔离站点", status_code=403)(
                    scope, receive, send
                )
                return
            headers[b"host"] = self.local
            if b"origin" in headers:
                headers[b"origin"] = b"http://" + self.local
            scope = dict(scope, headers=list(headers.items()))
        await self.app(scope, receive, send)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    dist = ROOT / "frontend/dist"
    formal = ROOT.parent / "StockMock"
    if (
        ROOT.resolve() == formal.resolve()
        or not 1024 <= args.port <= 65535
        or args.port == 8000
    ):
        parser.error("请使用隔离工作区和非 8000 的本机端口")
    if dist.resolve() != dist or not (dist / "index.html").is_file():
        parser.error("请先在当前隔离工作区构建前端，禁止使用外部 dist")
    secret = sys.stdin.readline().strip()
    if len(secret) < 32:
        parser.error("验收运行器须提供一次性随机测试口令")
    with (
        tempfile.TemporaryDirectory(prefix="stockmock-phase1-") as temp,
        ExitStack() as stack,
    ):
        for module in (
            "trading_routes",
            "asset_routes",
            "plan_routes",
            "record_routes",
            "market_routes",
            "storage",
        ):
            stack.enter_context(patch(f"app.{module}.datetime", Clock))
        stack.enter_context(patch("app.discovery.Discovery.search", return_value=()))
        prices = Prices()
        engine = Engine(Store(Path(temp) / "phase1.sqlite3"), prices, clock=lambda: NOW)
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.watchlist[:] = ["000001.SZ", "600519.SS", "AAPL"]
            state.cash["CNY"] -= Decimal("20000")
            state.lots.append(
                Lot(
                    symbol="600519.SS",
                    quantity=200,
                    cost=Decimal("20000"),
                    sellable_at=NOW,
                )
            )
            engine.store.save(connection, state)
        app = create_app(engine=engine, password=secret, run_worker=False)
        # 独立夹具故障开关，在静态文件兜底路由之前注册。
        static = app.router.routes.pop()
        assert static.name == "frontend"

        @app.post("/fixture/missing-price")
        def missing_price():
            prices.missing.set()
            return {"source": "受控行情", "symbol": "600519.SS"}

        @app.get("/fixture/status")
        def fixture_status():
            return {"name": "phase1-controlled", "port": args.port}

        app.router.routes.append(static)
        uvicorn.run(
            FixtureHost(app, args.port),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
        )


if __name__ == "__main__":
    main()
