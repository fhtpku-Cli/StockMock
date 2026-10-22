"""日期筛选回归服务：临时账本、受控行情、独立随机口令，不读取个人数据。"""

import os
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import uvicorn
from controlled_server import (
    CATALOG,
    QUOTE_TIME,
    TEST_NOW,
    ControlledProvider,
    Engine,
    PremarketClock,
    Store,
    create_app,
)
from fastapi.staticfiles import StaticFiles

if __name__ == "__main__":
    with (
        tempfile.TemporaryDirectory(prefix="stockmock-record-layout-") as temp,
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
            ControlledProvider(quote_time=QUOTE_TIME, history_day=QUOTE_TIME.date()),
            CATALOG,
        )
        app = create_app(
            engine=engine, password=os.environ["LAYOUT_TEST_PASSWORD"], run_worker=False
        )
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", None) != ""
        ]
        app.mount(
            "/",
            StaticFiles(directory=os.environ["LAYOUT_TEST_DIST"], html=True),
            name="layout-frontend",
        )
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(os.environ.get("LAYOUT_TEST_PORT", "8017")),
            log_level="warning",
        )
