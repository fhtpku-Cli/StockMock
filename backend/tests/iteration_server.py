# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fastapi", "uvicorn", "pydantic", "anyio", "exchange-calendars",
#   "httpx2[brotli,http2,zstd]", "quickjs-ng",
# ]
# ///
# 运行：PYTHONPATH=backend backend/.venv/bin/python backend/tests/iteration_server.py
"""0.2.0 浏览器候选夹具：真实 API/SQLite，仅时钟和外部行情受控。"""

import os
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal
from unittest.mock import patch
from uuid import uuid4

import uvicorn
from fastapi import Query
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from app.catalog import instrument
from app.engine import Engine
from app.main import create_app
from app.marketdata import Bar, FxRate, MarketDataError, Quote
from app.models import Entry, Journal, Model
from app.plan_alerts import due_alert
from app.plan_models import PlanInput
from app.plan_worker import PlanWorker
from app.plans import create_plan, round_plans
from app.storage import Store
from app.worker import Worker

DEFAULT_PRICE: Final = Decimal(100)
EVIDENCE: Final = Path(__file__).resolve().parents[2] / ".omo/evidence/iteration-0.2.0"


class Clock:
    """仅夹具可推进的市场时钟。"""

    current: ClassVar[datetime] = datetime(2026, 10, 23, 2, 0, tzinfo=UTC)

    @staticmethod
    def now(tz: tzinfo | None = None) -> datetime:
        return Clock.current.astimezone(tz or UTC)


class Control:
    """夹具允许在测试请求间切换价格与来源状态。"""

    price: ClassVar[Decimal] = Decimal(100)
    status: ClassVar[Literal["valid", "stale", "future", "suspended", "failure"]] = "valid"


class ControlResult(Model):
    """返回当前受控条件，不包含正式账户信息。"""

    price: Decimal | None = None
    status: str | None = None
    now: datetime | None = None
    added: int | None = None


@dataclass(frozen=True, slots=True)
class Provider:
    def quote(self, symbol: str) -> Quote:
        if Control.status == "failure":
            raise MarketDataError("controlled_failure", "受控来源失败")
        at = (
            Clock.current + timedelta(minutes=1)
            if Control.status == "future"
            else Clock.current - timedelta(minutes=30 if Control.status == "stale" else 1)
        )
        return Quote(
            symbol,
            Control.price,
            instrument(symbol).currency,
            at,
            Clock.current,
            "0.2.0受控验收行情",
            Control.status != "suspended",
            at - timedelta(minutes=1),
            Decimal(1000),
        )

    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        return (self.quote(symbol),)

    def fx_rates(self) -> tuple[FxRate, ...]:
        return (
            FxRate("USD", Decimal(7), Clock.current, Clock.current, "0.2.0受控汇率"),
            FxRate("HKD", Decimal(".9"), Clock.current, Clock.current, "0.2.0受控汇率"),
        )

    def history(self, symbol: str, *, retrieved_not_before: datetime | None = None) -> tuple[Bar, ...]:
        _ = retrieved_not_before
        quote = self.quote(symbol)
        return tuple(
            Bar(
                symbol,
                (Clock.current - timedelta(days=day)).date(),
                Decimal(100),
                Decimal(101),
                Decimal(99),
                Decimal(100),
                Decimal(1000),
                quote.currency,
                quote.market_time,
                Clock.current,
                "tencent-public",
            )
            for day in range(30, 0, -1)
        )


def run_server() -> None:
    """控制接口只存在于此隔离夹具，不注册到正式 app.main。"""
    os.environ["PAPER_ALLOWED_HOSTS"] = "127.0.0.1,localhost"
    with ExitStack() as stack:
        for module in (
            "app.trading_routes",
            "app.asset_routes",
            "app.record_routes",
            "app.storage",
            "app.auth",
            "app.plan_routes",
            "app.review_routes",
            "app.plans",
            "app.plan_worker",
            "app.worker",
        ):
            _ = stack.enter_context(patch(f"{module}.datetime", Clock))
        engine = Engine(Store(Path(os.getenv("CANDIDATE_DB", str(EVIDENCE / "candidate.sqlite3")))), Provider())
        if not round_plans(engine.store, engine.store.read().id):
            plan = create_plan(
                engine,
                PlanInput(
                    symbol="600519.SS",
                    direction="watch",
                    status="active",
                    reason="受控候选数据：观察计划与站内提示",
                    target_price=Decimal(100),
                    review_date=Clock.current.date(),
                ),
                Clock.current,
            )
            due_alert(engine.store, plan, Clock.current)
        app = create_app(engine, os.getenv("CANDIDATE_PASSWORD", "iteration-0.2.0-local-only"))
        app.router.routes[:] = [route for route in app.router.routes if not isinstance(route, Mount)]

        @app.post("/_test/advance")
        def advance(minutes: Annotated[int, Query(ge=1, le=100000)] = 5) -> ControlResult:
            Clock.current += timedelta(minutes=minutes)
            Worker(engine).tick()
            PlanWorker(engine).tick()
            return ControlResult(now=Clock.current)

        @app.post("/_test/control")
        def control(
            price: Annotated[Decimal, Query(gt=0)] = DEFAULT_PRICE,
            status: Literal["valid", "stale", "future", "suspended", "failure"] = "valid",
        ) -> ControlResult:
            Control.price, Control.status = price, status
            PlanWorker(engine).tick()
            return ControlResult(price=price, status=status)

        @app.post("/_test/history")
        def history_seed(count: Annotated[int, Query(ge=1, le=1000)] = 510) -> ControlResult:
            with engine.store.transaction() as connection:
                state = engine.store.load(connection)
                for index in range(count):
                    state.journal.append(
                        Journal(id=str(uuid4()), text=f"受控分页笔记 {index}", created_at=Clock.current)
                    )
                    state.ledger.append(
                        Entry(
                            id=str(uuid4()),
                            kind="controlled_history",
                            currency="CNY",
                            amount=Decimal(0),
                            note=f"受控分页零金额流水 {index}",
                            created_at=Clock.current,
                        )
                    )
                engine.store.save(connection, state)
            return ControlResult(added=count)

        app.mount("/", StaticFiles(directory=EVIDENCE / "candidate-dist", html=True, check_dir=False), name="candidate")
        uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CANDIDATE_PORT", "8200")), log_level="warning")


if __name__ == "__main__":
    run_server()
