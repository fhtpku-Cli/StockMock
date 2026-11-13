"""完整交易浏览器验收服务：临时 SQLite、受控时钟和测试文件驱动行情。

必须通过 FRONTEND_TEST_PORT 指定隔离端口，FRONTEND_TEST_DIST 指定本次独立构建。
FRONTEND_TEST_OUTPUT 指定证据目录，FRONTEND_TEST_PASSWORD 指定独立测试密码。控制文件由同机测试脚本写入，生产没有此入口。
运行：backend/.venv/bin/python frontend/tests/completion_server.py
"""

import json
import os
import tempfile
import threading
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# controlled_server 先设置既有后端与测试模块搜索路径。
from controlled_server import ROOT  # isort: skip

import test_engine
import uvicorn
from app.catalog import CATALOG
from app.engine import Engine
from app.main import create_app
from app.models import DomainError
from app.storage import Store
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount
from test_engine import ControlledProvider

OUTPUT = Path(os.environ["FRONTEND_TEST_OUTPUT"]).resolve()
DIST = Path(os.environ["FRONTEND_TEST_DIST"]).resolve()
PASSWORD = os.environ["FRONTEND_TEST_PASSWORD"]
PORT = int(os.environ["FRONTEND_TEST_PORT"])
PRODUCTION_PORT = 8000
if not PASSWORD or PORT == PRODUCTION_PORT or not (DIST / "index.html").is_file():
    raise SystemExit("必须提供独立口令、非正式端口及有效的独立构建目录")
if (ROOT / "frontend/dist").resolve() == DIST:
    raise SystemExit("完整流程测试不能使用默认构建目录")
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)


class Clock(datetime):
    """仅测试进程使用的显式市场时钟。"""

    value = NOW

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        """所有路由共享当前测试事件时间。"""
        return cls.value.astimezone(tz) if tz is not None else cls.value.replace(tzinfo=None)


def process_commands(engine: Engine, stop: threading.Event) -> None:
    """接收同机测试行情命令，将真实引擎结果写入回执。"""
    sequence = None
    while not stop.wait(0.1):
        command_path = OUTPUT / "control.json"
        if not command_path.exists():
            continue
        try:
            command = json.loads(command_path.read_text())
        except json.JSONDecodeError:
            continue
        if command["sequence"] == sequence:
            continue
        sequence = command["sequence"]
        try:
            start = Clock.value.replace(minute=(Clock.value.minute // 5) * 5, second=0, microsecond=0)
            if start < Clock.value:
                start += timedelta(minutes=5)
            market_time = start + timedelta(minutes=5)
            Clock.value = market_time + timedelta(seconds=1)
            test_engine.NOW = Clock.value
            engine.provider = ControlledProvider(
                quote_time=market_time,
                price=Decimal(command["price"]),
                fx_available=command.get("fx_available", True),
                history_day=Clock.value.date() - timedelta(days=1),
            )
            quote = replace(engine.provider.quote(command["symbol"]), interval_start=start)
            engine.process(command["symbol"], (quote,), Clock.value)
            result = {"sequence": sequence, "ok": True}
        except (DomainError, ValueError, KeyError) as error:
            result = {"sequence": sequence, "ok": False, "error": str(error)}
        (OUTPUT / "ack.json").write_text(json.dumps(result))


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("control.json", "ack.json"):
        (OUTPUT / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="stockmock-flow-") as temp, ExitStack() as patches:
        for module in (
            "app.trading_routes",
            "app.asset_routes",
            "app.record_routes",
            "app.personal_routes",
            "app.storage",
            "app.auth",
        ):
            patches.enter_context(patch(f"{module}.datetime", Clock))
        engine = Engine(
            Store(Path(temp) / "ledger.sqlite3"),
            ControlledProvider(quote_time=NOW - timedelta(minutes=1), history_day=NOW.date() - timedelta(days=1)),
            CATALOG,
        )
        stop = threading.Event()
        thread = threading.Thread(target=process_commands, args=(engine, stop), daemon=True)
        # ControlledProvider references this module-level value for FX freshness.
        patches.enter_context(patch("test_engine.NOW", NOW))
        thread.start()
        try:
            app = create_app(engine=engine, password=PASSWORD, run_worker=False)
            app.router.routes[:] = [
                route for route in app.router.routes if not (isinstance(route, Mount) and route.name == "frontend")
            ]
            app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=PORT,
                log_level="warning",
            )
        finally:
            stop.set()
            thread.join(timeout=2)
