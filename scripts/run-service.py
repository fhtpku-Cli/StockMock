#!/usr/bin/env python3
"""后台专用启动器：使用已安装依赖，密码仅在进程内读取，日志滚动保存。"""

from __future__ import annotations
from pydantic import TypeAdapter
import logging
import logging.handlers
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_private(path: Path) -> str:
    """拒绝链接和非本人私密文件，避免意外公开配置。"""
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
    ):
        raise RuntimeError(f"文件须由当前用户拥有且权限为 0600：{path}")
    return path.read_text()


def configuration(root: Path) -> dict[str, str]:
    """只接受白名单 JSON 设置；不执行 shell 文件。"""
    path = root / "backend/.runtime.json"
    if not path.exists():
        return {}
    raw = TypeAdapter(dict[str, str]).validate_json(read_private(path), strict=True)
    allowed = {
        "PAPER_ALLOWED_HOSTS",
        "PAPER_ALLOWED_ORIGINS",
        "PAPER_SECURE_COOKIE",
        "PAPER_PAUSED_SYMBOLS",
        "STOCKMOCK_INSTRUMENTS_FILE",
        "STOCKMOCK_FUTU_PORT",
        "STOCKMOCK_FUTU_BAR_LABEL",
    }
    if any(key not in allowed for key in raw):
        raise RuntimeError("运行配置只接受允许的字符串设置，不接受密码、命令或其他字段")
    return raw


def configure_logging(log_dir: Path) -> logging.handlers.RotatingFileHandler:
    """运行日志限制为当前文件加三份历史，不输出访问请求。"""
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "service.log", maxBytes=1_000_000, backupCount=3
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return handler


def main() -> None:
    """监听回环地址，崩溃后由 LaunchAgent 重新启动。"""
    _ = os.umask(0o077)
    config = configuration(ROOT)
    password = read_private(ROOT / "backend/.local-password").strip()
    if len(password) < 12:
        raise RuntimeError("访问密码至少需要 12 字符")
    for key in (
        "PAPER_ALLOWED_HOSTS",
        "PAPER_ALLOWED_ORIGINS",
        "PAPER_SECURE_COOKIE",
        "PAPER_PAUSED_SYMBOLS",
        "STOCKMOCK_INSTRUMENTS_FILE",
        "STOCKMOCK_FUTU_PORT",
        "STOCKMOCK_FUTU_BAR_LABEL",
    ):
        _ = os.environ.pop(key, None)
    os.environ.update(config)
    os.environ["PAPER_DB"] = str(ROOT / "backend/data/paper.sqlite3")
    # 导入应用后显式传入密码，不把密码放到 plist、启动参数或进程环境。
    sys.path.insert(0, str(ROOT / "backend"))
    import uvicorn
    from app.main import create_app

    log_dir = ROOT / "backend/data/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _ = configure_logging(log_dir)
    app = create_app(password=password)
    if "--check-config" in sys.argv:
        print("运行配置、密码权限与依赖检查通过；未启动服务。")
        return
    uvicorn.run(
        app, host="127.0.0.1", port=8000, workers=1, log_config=None, access_log=False
    )


if __name__ == "__main__":
    main()
