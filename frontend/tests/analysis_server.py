"""0.3.0 浏览器隔离夹具：真实 API/SQLite，行情、时间与示例账本受控。"""
import os
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Literal
from unittest.mock import patch
from uuid import uuid4

import uvicorn
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from app.adjustments import apply_action
from app.catalog import instrument
from app.engine import Engine
from app.main import create_app
from app.marketdata import Bar, FxRate, MarketDataError, Quote
from app.models import Entry, Lot, Order
from app.personal_models import AdjustmentInput, ValuationSnapshot
from app.storage import Store

ROOT = Path(__file__).resolve().parents[2]

class Clock:
    current: ClassVar[datetime] = datetime(2026, 10, 23, 2, tzinfo=UTC)

    @staticmethod
    def now(tz: tzinfo | None = None) -> datetime:
        return Clock.current.astimezone(tz or UTC)

class Provider:
    fx_missing = False
    quote_missing = False

    def quote(self, symbol: str) -> Quote:
        if self.quote_missing and symbol == "MSFT":
            raise MarketDataError("controlled_failure", "受控缺失行情")
        at = Clock.current - timedelta(minutes=1)
        amount = Decimal(100) if symbol in {"AAPL", "MSFT"} else Decimal(10)
        return Quote(symbol, amount, instrument(symbol).currency, at, Clock.current, "0.3.0受控行情", True, at - timedelta(minutes=1), Decimal(1000))

    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        return (self.quote(symbol),)

    def fx_rates(self) -> tuple[FxRate, ...]:
        if self.fx_missing:
            raise MarketDataError("controlled_fx", "受控缺失汇率")
        return (FxRate("USD", Decimal(7), Clock.current, Clock.current, "受控汇率"), FxRate("HKD", Decimal(".9"), Clock.current, Clock.current, "受控汇率"))

    def history(self, symbol: str) -> tuple[Bar, ...]:
        return ()


def seed(engine: Engine) -> None:
    start = datetime(2026, 10, 1, 2, tzinfo=UTC)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection).model_copy(update={"created_at": start})
        if state.ledger:
            return
        state.cash.update({"CNY": Decimal(990000), "USD": Decimal(100), "HKD": Decimal(50)})
        state.lots.extend([
            Lot(symbol="510300.SS", quantity=100, cost=Decimal(900), sellable_at=start),
            Lot(symbol="AAPL", quantity=10, cost=Decimal(800), sellable_at=start),
            Lot(symbol="MSFT", quantity=10, cost=Decimal(900), sellable_at=start),
        ])
        order = Order(id=str(uuid4()), request_id="controlled-filled-order", symbol="510300.SS", side="buy", quantity=100, limit_price=Decimal(10), price_bound=Decimal(10), status="filled", created_at=start, active_at=start, expires_at=start + timedelta(days=1), filled_at=start, confirmed_at=start, fill_price=Decimal(10), fee=Decimal(".5"), currency="CNY", source="受控历史委托", reason="只用于浏览器关联详情验证")
        state.orders.append(order)
        for currency, amount in [("CNY", "-10000"), ("USD", "100"), ("HKD", "50")]:
            state.ledger.append(Entry(id=str(uuid4()), kind="controlled_cash", currency=currency, amount=Decimal(amount), created_at=start, order_id=order.id if currency == "CNY" else None, note="受控初始现金变化"))
        for index in range(520):
            state.ledger.append(Entry(id=str(uuid4()), kind="legacy_unknown" if index % 2 else "split", currency="CNY", amount=Decimal(0), created_at=Clock.current, note=f"受控分页 {index} 中文长说明" + "保持可读" * 12))
        state.ledger.append(Entry(id=str(uuid4()), kind="legacy_unknown", currency="CNY", amount=Decimal(0), created_at=Clock.current, note="=SUM(1,2)\n中文逗号,自由文本"))
        engine.store.save(connection, state)
        for day, amount in [(1, "1000000"), (21, "1000000"), (23, "1001200")]:
            at = datetime(2026, 10, day, 2, tzinfo=UTC)
            point = ValuationSnapshot(id=str(uuid4()), round_id=state.id, day=at.date(), captured_at=at, total_assets_cny=Decimal(amount), pnl_cny=Decimal(amount)-Decimal(1000000), status="complete", issues=[], price_times={"FX:USD": at, "AAPL": at})
            connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (state.id, str(point.day), point.model_dump_json()))
    apply_action(engine, AdjustmentInput(request_id="controlled-dividend", round_id=state.id, symbol="AAPL", kind="dividend", effective_date=(Clock.current - timedelta(days=1)).date(), expected_quantity=10, note="受控应收股息记录", cash_per_share=Decimal(2), payment_status="receivable"), Clock.current)


def run() -> None:
    os.environ["PAPER_ALLOWED_HOSTS"] = "127.0.0.1,localhost"
    database = Path(os.environ["CANDIDATE_DB"]).resolve()
    evidence = (ROOT / ".omo/evidence/0.3.0/frontend").resolve()
    if not database.is_relative_to(evidence):
        raise ValueError("夹具只允许写入本轮前端证据目录")
    with ExitStack() as stack:
        for module in ("app.trading_routes", "app.asset_routes", "app.record_routes", "app.storage", "app.auth", "app.analysis_routes", "app.personal_routes", "app.plan_routes", "app.review_routes", "app.plans", "app.plan_worker", "app.worker"):
            stack.enter_context(patch(f"{module}.datetime", Clock))
        provider = Provider()
        engine = Engine(Store(database), provider)
        seed(engine)
        app = create_app(engine, "analysis-local-only", run_worker=False)
        app.router.routes[:] = [route for route in app.router.routes if not isinstance(route, Mount)]

        @app.post("/_test/control")
        def control(mode: Literal["normal", "fx", "quote", "difference", "append", "endpoint"] = "normal") -> dict[str, str]:
            provider.fx_missing = mode == "fx"
            provider.quote_missing = mode == "quote"
            with engine.store.transaction() as connection:
                state = engine.store.load(connection)
                if mode == "difference":
                    state.cash["CNY"] += Decimal(1)
                    engine.store.save(connection, state)
                if mode == "append":
                    state.ledger.append(Entry(id=str(uuid4()), kind="new_entry", currency="CNY", amount=Decimal(0), created_at=Clock.current, note="分页过程中新增记录"))
                    engine.store.save(connection, state)
                if mode == "endpoint":
                    point = ValuationSnapshot(id=str(uuid4()), round_id=state.id, day=Clock.current.date(), captured_at=Clock.current, total_assets_cny=None, pnl_cny=None, status="missing", issues=["受控端点缺失"], price_times={})
                    connection.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?)", (state.id, str(point.day), point.model_dump_json()))
            return {"mode": mode}

        app.mount("/", StaticFiles(directory=os.environ["CANDIDATE_DIST"], html=True), name="candidate")
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CANDIDATE_PORT", "8303")), log_level="warning")

if __name__ == "__main__":
    run()
