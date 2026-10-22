"""完整个人功能的隔离浏览器服务；固定盘前时钟、临时账本及明确测试持仓。

仅此进程使用种子持仓和历史快照，生产行情与数据库不受影响。
运行：backend/.venv/bin/python frontend/tests/personal_server.py
"""

import sys
import tempfile
from contextlib import ExitStack
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from controlled_server import ROOT, TEST_NOW, QUOTE_TIME, PremarketClock

from app.catalog import CATALOG
from app.engine import Engine
from app.main import create_app
from app.models import Lot
from app.personal_models import ValuationSnapshot
from app.storage import Store
from test_engine import ControlledProvider
import uvicorn

if __name__ == "__main__":
    with (
        tempfile.TemporaryDirectory(prefix="stockmock-personal-ui-") as temp,
        ExitStack() as clocks,
    ):
        for module in (
            "app.trading_routes",
            "app.asset_routes",
            "app.record_routes",
            "app.personal_routes",
            "app.storage",
            "app.auth",
        ):
            clocks.enter_context(patch(f"{module}.datetime", PremarketClock))
        clocks.enter_context(patch("test_engine.NOW", TEST_NOW))
        engine = Engine(
            Store(Path(temp) / "test.sqlite3"),
            ControlledProvider(quote_time=QUOTE_TIME, history_day=QUOTE_TIME.date()),
            tuple(
                item.model_copy(
                    update={
                        "tradable": True,
                        "restriction": "受控测试行情，仅供界面验证",
                    }
                )
                for item in CATALOG
            ),
        )
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.cash["CNY"] -= Decimal("10005")
            state.lots.append(
                Lot(
                    symbol="600519.SS",
                    quantity=100,
                    cost=Decimal("10005"),
                    sellable_at=TEST_NOW - timedelta(days=2),
                )
            )
            engine.store.save(connection, state)
            for offset, status in (
                (7, "complete"),
                (6, "complete"),
                (5, "missing"),
                (4, "complete"),
                (3, "stale"),
                (1, "complete"),
            ):
                moment = TEST_NOW - timedelta(days=offset)
                snapshot = ValuationSnapshot(
                    id=str(uuid4()),
                    round_id=state.id,
                    day=moment.date(),
                    captured_at=moment,
                    total_assets_cny=None
                    if status == "missing"
                    else Decimal("1000000"),
                    pnl_cny=None if status == "missing" else Decimal(0),
                    status=status,
                    issues=[] if status == "complete" else ["受控测试：验证估值缺口"],
                    price_times={},
                )
                connection.execute(
                    "INSERT INTO snapshots VALUES(?,?,?)",
                    (state.id, str(snapshot.day), snapshot.model_dump_json()),
                )
        password = (ROOT / "backend/.local-password").read_text().strip()
        uvicorn.run(
            create_app(engine=engine, password=password, run_worker=False),
            host="127.0.0.1",
            port=8002,
            log_level="warning",
        )
