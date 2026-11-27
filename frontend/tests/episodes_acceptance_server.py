"""04 建仓到清仓与后台中断记录的隔离验收。仅使用候选页面、临时 SQLite、受控时钟与受控数据。"""

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
import json
from uuid import uuid4

from app.models import Lot
from app.personal_models import Adjustment
from app.storage import Store
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


UTC_ = UTC


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """10 月某日的 UTC 时刻。"""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def filled(
    identifier: str,
    symbol: str,
    side: str,
    quantity: int,
    price: str,
    when: datetime,
    currency: str = "CNY",
):
    order = make_order(
        identifier,
        status="filled",
        side=side,  # pyright: ignore[reportArgumentType]
        symbol=symbol,
        currency=currency,  # pyright: ignore[reportArgumentType]
        quantity=quantity,
        confirmed_at=when + timedelta(minutes=1),
        filled_at=when,
        fill_price=Decimal(price),
    )
    fee = (Decimal(price) * quantity * Decimal("0.0005")).quantize(Decimal("0.01"))
    return order.model_copy(
        update={"fee": fee, "status_reason": "按有效市场样本整笔成交；不模拟盘口排队"}
    )


def adjustment(
    round_id: str, symbol: str, kind: str, effective: date, before: int, after: int, **extra
) -> Adjustment:
    return Adjustment(
        request_id=f"request-{uuid4()}",
        round_id=round_id,
        symbol=symbol,
        kind=kind,  # pyright: ignore[reportArgumentType]
        effective_date=effective,
        expected_quantity=before,
        note="受控验收数据，非真实公司行动",
        id=str(uuid4()),
        created_at=datetime.combine(effective, datetime.min.time(), UTC) - timedelta(hours=10),
        quantity_before=before,
        quantity_after=after,
        cash_delta=extra.pop("cash_delta", Decimal(0)),
        receivable_amount=extra.pop("receivable_amount", Decimal(0)),
        currency="USD",
        cost_before=Decimal("1990.00"),
        cost_after=Decimal("1990.00"),
        **extra,
    )


def seed(engine: Engine) -> None:
    """北京时间 10/22 10:00；茅台两段（第二段进行中），AAPL 含拆股与应收股息。"""
    with engine.store.transaction() as connection:
        state = engine.store.load(connection).model_copy(
            update={"created_at": NOW - timedelta(days=12)}
        )
        state.watchlist[:] = ["600519.SS", "AAPL"]
        state.orders.extend(
            [
                filled("m1", "600519.SS", "buy", 100, "1450.00", at(12, 1, 35)),
                filled("m2", "600519.SS", "buy", 100, "1470.00", at(13, 2, 0)),
                filled("m3", "600519.SS", "sell", 50, "1490.00", at(14, 5, 30)),
                filled("m4", "600519.SS", "sell", 150, "1500.00", at(15, 6, 0)),
                filled("m5", "600519.SS", "buy", 100, "1460.00", at(20, 1, 40)),
                filled("a1", "AAPL", "buy", 10, "199.00", at(13, 14, 0), "USD"),
                filled("a2", "AAPL", "sell", 5, "101.00", at(19, 15, 0), "USD"),
            ]
        )
        state.lots.extend(
            [
                Lot(symbol="600519.SS", quantity=100, cost=Decimal("146073.00"), sellable_at=NOW),
                Lot(symbol="AAPL", quantity=15, cost=Decimal("1492.50"), sellable_at=NOW),
            ]
        )
        engine.store.save(connection, state)
        actions = [
            adjustment(
                state.id, "AAPL", "split", date(2026, 10, 15), 10, 20, numerator=2, denominator=1
            ),
            adjustment(
                state.id,
                "AAPL",
                "dividend",
                date(2026, 10, 16),
                20,
                20,
                cash_per_share=Decimal("0.25"),
                payment_status="receivable",
                receivable_amount=Decimal("5.00"),
            ),
        ]
        for action in actions:
            _ = connection.execute(
                "INSERT INTO adjustments VALUES(?,?,?,?,?,?)",
                (
                    action.id,
                    state.id,
                    action.symbol,
                    action.kind,
                    str(action.effective_date),
                    action.model_dump_json(),
                ),
            )
        gaps = [
            (at(21, 14, 30), at(21, 16, 10), ["US"]),
            (at(19, 1, 20), at(19, 9, 50), ["CN", "HK"]),
        ]
        for started, ended, markets in gaps:
            _ = connection.execute(
                "INSERT INTO worker_gaps VALUES(?,?,?,?,?)",
                (
                    started.isoformat(),
                    started.isoformat(),
                    ended.isoformat(),
                    json.dumps(markets),
                    "paused_or_down",
                ),
            )


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
    with tempfile.TemporaryDirectory(prefix="stockmock-04-") as temp, ExitStack() as stack:
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
