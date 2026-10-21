"""隔离界面测试服务：真实交易引擎、临时 SQLite、受控行情。

运行：backend/.venv/bin/python frontend/tests/controlled_server.py
固定测试时钟：2026-10-22 08:00:00 北京时间，始终在 A 股开盘前。
时钟每次读取只推进一微秒，保证创建记录的先后顺序，不依赖实际日期。
报价为上一交易日 14:55 的完整样本，历史收盘日明确为 2026-10-21。
时钟替换仅在此独立测试进程中生效；生产代码没有测试时钟入口。
"""

import sys
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta, tzinfo
from itertools import count
from pathlib import Path
from typing import Final
from unittest.mock import patch

ROOT: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/tests"))

import uvicorn

from app.catalog import CATALOG
from app.engine import Engine
from app.main import create_app
from app.storage import Store
from test_engine import ControlledProvider

TEST_NOW: Final = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
QUOTE_TIME: Final = datetime(2026, 10, 21, 6, 55, tzinfo=UTC)


class PremarketClock(datetime):
    """测试进程专用时钟；微秒序号保留记录顺序，无实际时钟输入。"""

    ticks: Final = count()

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        """返回固定盘前时间线上的下一个时间戳。"""
        value = TEST_NOW + timedelta(microseconds=next(cls.ticks))
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


if __name__ == "__main__":
    with (
        tempfile.TemporaryDirectory(prefix="portfolio-ui-controlled-") as temp,
        ExitStack() as clocks,
    ):
        for module in (
            "app.trading_routes",
            "app.asset_routes",
            "app.record_routes",
            "app.storage",
            "app.auth",
        ):
            clocks.enter_context(patch(f"{module}.datetime", PremarketClock))
        clocks.enter_context(patch("test_engine.NOW", TEST_NOW))
        engine = Engine(
            Store(Path(temp) / "test.sqlite3"),
            ControlledProvider(
                quote_time=QUOTE_TIME,
                history_day=QUOTE_TIME.date(),
            ),
            tuple(
                item.model_copy(
                    update={
                        "tradable": True,
                        "restriction": "受控测试行情，仅用于界面验收",
                    }
                )
                for item in CATALOG
            ),
        )
        password = (ROOT / "backend/.local-password").read_text().strip()
        app = create_app(engine=engine, password=password, run_worker=False)
        uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
