#!/usr/bin/env python3
"""后台专用启动器：使用已安装依赖，密码仅在进程内读取，日志滚动保存。"""

from __future__ import annotations
import logging
import sqlite3
import tempfile
import time
from contextlib import closing
import logging.handlers
import os
import stat
import sys
from pathlib import Path
from typing import override

ROOT = Path(__file__).resolve().parents[1]


def read_private(path: Path) -> str:
    """拒绝链接和非本人私密文件，避免意外公开配置。"""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise RuntimeError(f"文件须由当前用户拥有且权限为 0600：{path}")
    return path.read_text()


def configuration(root: Path) -> dict[str, str]:
    """只接受白名单 JSON 设置；不执行 shell 文件。"""
    path = root / "backend/.runtime.json"
    if not path.exists() and not path.is_symlink():
        return {}
    from pydantic import TypeAdapter

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
    log_dir.mkdir(parents=True, exist_ok=True)
    if log_dir.is_symlink() or log_dir.stat().st_uid != os.getuid():
        raise RuntimeError("日志目录须由当前用户拥有且不得为链接")
    if not log_dir.stat().st_mode & stat.S_IWUSR:
        raise PermissionError("日志目录不可写")
    log_dir.chmod(0o700)
    for path in (log_dir / f"service.log{suffix}" for suffix in ("", ".1", ".2", ".3")):
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError("日志文件须为本人普通文件")
            path.chmod(0o600)
    handler = PrivateLogHandler(log_dir / "service.log", maxBytes=1_000_000, backupCount=3)
    (log_dir / "service.log").chmod(0o600)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return handler


class PrivateLogHandler(logging.handlers.RotatingFileHandler):
    """日志写入失败交还入口处理，避免 logging 回显异常和原始记录。"""

    @override
    def doRollover(self) -> None:
        previous = os.umask(0o077)
        try:
            super().doRollover()
        finally:
            _ = os.umask(previous)

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        raise OSError("私有日志写入失败")


DIAGNOSTICS = {
    "logging": "诊断日志不可写；请检查日志目录权限；未确认记录日志。",
    "config_file": "运行配置文件缺失、不可读或权限不安全。",
    "config_format": "运行配置 JSON、字段或类型无效。",
    "password_file": "密码文件缺失、不可读或权限不安全。",
    "password_value": "访问密码至少需要 12 字符。",
    "dependency": "运行依赖无法导入；请按锁文件安装依赖。",
    "initialization": "应用初始化失败；请核对 Host/Origin、证券规则与数据库。",
    "startup": "服务启动失败；请检查端口与系统任务状态。",
}


def main() -> None:
    """先建立安全诊断，再校验配置；检查仅初始化临时副本。"""
    _ = os.umask(0o077)
    stage = "logging"
    handler: logging.handlers.RotatingFileHandler | None = None
    try:
        handler = configure_logging(ROOT / "backend/data/logs")
        stage = "config_file"
        config_path = ROOT / "backend/.runtime.json"
        if config_path.exists() or config_path.is_symlink():
            _ = read_private(config_path)
        stage = "config_format"
        config = configuration(ROOT)
        stage = "password_file"
        password = read_private(ROOT / "backend/.local-password").strip()
        stage = "password_value"
        if len(password) < 12:
            raise RuntimeError("密码过短")
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
        database = ROOT / "backend/data/paper.sqlite3"
        os.environ["PAPER_DB"] = str(database)
        sys.path.insert(0, str(ROOT / "backend"))
        stage = "dependency"
        import uvicorn

        stage = "initialization"
        from app.main import create_app

        stage = "initialization"
        if "--check-config" in sys.argv:
            with tempfile.TemporaryDirectory(prefix="stockmock-check-") as temp:
                isolated = Path(temp) / "paper.sqlite3"
                if database.exists():
                    deadline = time.monotonic() + 5

                    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("数据库副本读取超时")

                    with (
                        closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source,
                        closing(sqlite3.connect(isolated)) as destination,
                    ):
                        source.backup(destination, pages=256, progress=check_deadline, sleep=0.05)
                os.environ["PAPER_DB"] = str(isolated)
                try:
                    _ = create_app(password=password, run_worker=False)
                finally:
                    os.environ["PAPER_DB"] = str(database)
            print("配置与依赖检查通过；未启动服务，未验证行情或后台运行。")
            return
        app = create_app(password=password)
        stage = "startup"
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8000,
            workers=1,
            log_config=None,
            access_log=False,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
    except Exception as error:
        # 唯一进程边界只输出固定诊断，不让第三方异常回显私有输入。
        if isinstance(error, ImportError):
            stage = "dependency"
        message = f"[{stage}] {DIAGNOSTICS[stage]}"
        if handler is not None:
            try:
                handler.emit(logging.LogRecord("startup", logging.ERROR, "", 0, message, (), None))
                handler.flush()
            except Exception:
                # 日志写入失败也只报告固定类别，禁止回显原记录。
                print(f"[logging] {DIAGNOSTICS['logging']}", file=sys.stderr)
        print(message, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
