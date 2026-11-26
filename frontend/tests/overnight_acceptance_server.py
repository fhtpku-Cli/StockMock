"""09 最近结束委托与 12 资产回落的隔离验收。仅使用候选页面、临时 SQLite、受控时钟与受控数据。"""

import argparse
import sys
import tempfile
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import uvicorn
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.engine import Engine
from app.main import create_app
from app.models import Lot
from app.storage import Store
from tests.test_drawdown import point, save_points
from tests.test_engine import NOW, ControlledProvider
from tests.test_order_endings import make_entry, make_order

ROOT = Path(__file__).resolve().parents[2]
SITE = "accept.localhost"


class Clock(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class FixtureHost:
    """以独立浏览器域名隔离 Cookie。仅转发此测试站点的请求到本机应用。"""

    def __init__(self, app: ASGIApp, port: int):
        self.app = app
        self.site = f"{SITE}:{port}".encode()
        self.local = f"127.0.0.1:{port}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if (
                headers.get(b"host") != self.site
                or headers.get(b"origin", b"http://" + self.site) != b"http://" + self.site
            ):
                await PlainTextResponse("仅限隔离站点", status_code=403)(scope, receive, send)
                return
            headers[b"host"] = self.local
            if b"origin" in headers:
                headers[b"origin"] = b"http://" + self.local
            scope = dict(scope, headers=list(headers.items()))
        await self.app(scope, receive, send)


def seed(engine: Engine) -> None:
    """北京时间 10/22 10:00；窗口为 10/20 00:00 起。"""
    with engine.store.transaction() as connection:
        state = engine.store.load(connection).model_copy(
            update={"created_at": NOW - timedelta(days=12)}
        )
        state.watchlist[:] = ["000001.SZ", "600519.SS", "AAPL"]
        state.lots.append(
            Lot(symbol="600519.SS", quantity=200, cost=Decimal("20000"), sellable_at=NOW)
        )
        delayed = make_order(
            "a-delayed",
            status="filled",
            symbol="600519.SS",
            quantity=100,
            confirmed_at=NOW - timedelta(minutes=40),
            filled_at=NOW - timedelta(hours=8, minutes=25),
            fill_price=Decimal("1465.500"),
            reserved_cny=Decimal("147623.28"),
        ).model_copy(
            update={
                "status_reason": "按有效市场样本整笔成交；不模拟盘口排队；后台延迟处理，按市场时间 09:35 的完整区间成交"
            }
        )
        usd = make_order(
            "b-usd",
            status="filled",
            symbol="AAPL",
            currency="USD",
            quantity=5,
            confirmed_at=NOW - timedelta(hours=6),
            filled_at=NOW - timedelta(hours=6),
            fill_price=Decimal("199.00"),
            reserved_cny=Decimal("7200.00"),
        ).model_copy(update={"status_reason": "按有效市场样本整笔成交；不模拟盘口排队"})
        precancel = make_order(
            "c-precancel",
            status="cancelled",
            symbol="000001.SZ",
            quantity=1000,
            confirmed_at=None,
            cancel_at=NOW - timedelta(days=1),
            reserved_cny=Decimal("11005.50"),
        ).model_copy(update={"status_reason": "预挂委托已撤销"})
        expired_sell = make_order(
            "d-expired-sell",
            status="expired",
            side="sell",
            symbol="600519.SS",
            quantity=100,
            confirmed_at=datetime(2026, 10, 20, 7, 0, tzinfo=UTC),
        ).model_copy(update={"status_reason": "当日有效委托到期，未成交"})
        outside = make_order(
            "e-outside", status="expired", confirmed_at=datetime(2026, 10, 19, 15, 59, tzinfo=UTC)
        )
        active = make_order("f-active", status="accepted", confirmed_at=None)
        state.orders.extend([outside, expired_sell, precancel, usd, delayed, active])
        state.ledger.extend(
            [
                make_entry("l1", "CNY", "-146550.00", order_id="a-delayed"),
                make_entry("l2", "CNY", "-73.28", order_id="a-delayed", kind="fee"),
                make_entry("l3", "CNY", "-6633.07", order_id="b-usd", kind="fx_out"),
                make_entry("l4", "USD", "995.50", order_id="b-usd", kind="fx_in"),
                make_entry("l5", "USD", "-995.00", order_id="b-usd"),
                make_entry("l6", "USD", "-0.50", order_id="b-usd", kind="fee"),
            ]
        )
        engine.store.save(connection, state)
        round_id = state.id
    values = {
        12: "1000000.00",
        13: "1012000.00",
        14: "1030000.00",
        15: "995000.00",
        16: "980500.00",
        18: "990000.00",
        19: "1001000.00",
        21: "1005000.00",
        22: "1008000.00",
    }
    points = [
        point(date(2026, 10, day), amount).model_copy(update={"round_id": round_id})
        for day, amount in values.items()
    ]
    points.append(
        point(date(2026, 10, 20), None, status="stale").model_copy(update={"round_id": round_id})
    )
    save_points(engine, points)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    dist = ROOT / "frontend/dist"
    formal = ROOT.parent / "StockMock"
    if ROOT.resolve() == formal.resolve() or not 1024 <= args.port <= 65535 or args.port == 8000:
        parser.error("请使用隔离工作区和非 8000 的本机端口")
    if not (dist / "index.html").is_file():
        parser.error("请先在当前隔离工作区构建前端")
    secret = sys.stdin.readline().strip()
    if len(secret) < 32:
        parser.error("验收运行器须提供一次性随机测试口令")
    with tempfile.TemporaryDirectory(prefix="stockmock-0912-") as temp, ExitStack() as stack:
        for module in (
            "trading_routes",
            "asset_routes",
            "analysis_routes",
            "personal_routes",
            "record_routes",
            "market_routes",
            "storage",
        ):
            stack.enter_context(patch(f"app.{module}.datetime", Clock))
        stack.enter_context(patch("app.discovery.Discovery.search", return_value=()))
        engine = Engine(
            Store(Path(temp) / "accept.sqlite3"), ControlledProvider(), clock=lambda: NOW
        )
        seed(engine)
        app = create_app(engine=engine, password=secret, run_worker=False)
        uvicorn.run(
            FixtureHost(app, args.port),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
        )


if __name__ == "__main__":
    main()
