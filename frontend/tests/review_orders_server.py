"""E1 隔离浏览器服务。使用项目已锁定的 backend/.venv，不增加依赖。

运行：配置 FRONTEND_TEST_PORT/DIST/OUTPUT/PASSWORD 后执行
backend/.venv/bin/python frontend/tests/review_orders_server.py
只播种受控历史，不访问正式数据库；停止服务即删除临时SQLite。
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/tests"))

import uvicorn
from app.catalog import CATALOG
from app.engine import Engine
from app.main import create_app
from app.storage import Store
from fastapi.staticfiles import StaticFiles
from review_orders_fixtures import NOW, seed
from starlette.routing import Mount
from test_engine import ControlledProvider


def main() -> None:
    output = Path(os.environ["FRONTEND_TEST_OUTPUT"]).resolve()
    dist = Path(os.environ["FRONTEND_TEST_DIST"]).resolve()
    password = os.environ["FRONTEND_TEST_PASSWORD"]
    port = int(os.environ["FRONTEND_TEST_PORT"])
    if not password or not 1024 <= port <= 65535 or port == 8000:
        raise SystemExit("必须指定临时密码与非正式端口")
    if (
        dist == (ROOT / "frontend/dist").resolve()
        or not (dist / "index.html").is_file()
    ):
        raise SystemExit("必须指定有效独立构建目录，不允许默认dist")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stockmock-e1-") as temp:
        engine = Engine(
            Store(Path(temp) / "ledger.sqlite3"),
            ControlledProvider(quote_time=NOW),
            CATALOG,
        )
        fixture = seed(engine)
        (output / "fixture.json").write_text(fixture.model_dump_json(indent=2))
        (output / "server.json").write_text(
            '{"host":"127.0.0.1","port":'
            + str(port)
            + ',"pid":'
            + str(os.getpid())
            + ',"run_worker":false,"temporary_database":true,"controlled_history":true}'
        )
        app = create_app(engine=engine, password=password, run_worker=False)
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if not (isinstance(route, Mount) and route.name == "frontend")
        ]
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
