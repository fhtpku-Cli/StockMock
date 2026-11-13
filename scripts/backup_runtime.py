"""私有运行材料和在线 SQLite 一致副本。"""

from __future__ import annotations
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Final
from pydantic import TypeAdapter
from .backup_files import absolute, read, regular, require

ALLOWED: Final = {
    "PAPER_ALLOWED_HOSTS",
    "PAPER_ALLOWED_ORIGINS",
    "PAPER_SECURE_COOKIE",
    "PAPER_PAUSED_SYMBOLS",
    "STOCKMOCK_INSTRUMENTS_FILE",
    "STOCKMOCK_FUTU_PORT",
    "STOCKMOCK_FUTU_BAR_LABEL",
}


def runtime(root: Path) -> dict[str, bytes]:
    config_bytes = read(root / "backend/.runtime.json", private=True)
    config = TypeAdapter(dict[str, str]).validate_json(config_bytes, strict=True)
    require(config.keys() <= ALLOWED, "config_fields")
    password = read(root / "backend/.local-password", private=True)
    require(len(password.decode().strip()) >= 12, "password_length")
    files = {"config.json": config_bytes, "password": password}
    if "STOCKMOCK_INSTRUMENTS_FILE" in config:
        external = Path(config["STOCKMOCK_INSTRUMENTS_FILE"])
        require(bool(config["STOCKMOCK_INSTRUMENTS_FILE"]), "rules_path")
        external = absolute(external if external.is_absolute() else root / external)
        files["instruments.json"] = read(external)
    return files


def database(path: Path) -> dict[str, int]:
    """immutable 检查仅限已完成副本，不创建 WAL/SHM。"""
    _ = regular(path, private=True)
    require(
        all(not Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")),
        "database_sidecar",
    )
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True, timeout=0.1)) as connection:
        deadline = time.monotonic() + 5
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        require(
            connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
            "database_integrity",
        )
        rows = TypeAdapter(list[tuple[str]]).validate_python(
            connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(),
            strict=True,
        )
        require(bool(rows), "database_empty")
        counts: dict[str, int] = {}
        for (name,) in rows:
            count = TypeAdapter(tuple[int]).validate_python(
                connection.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone(),
                strict=True,
            )
            counts[name] = count[0]
        return counts


def online(source: Path, destination: Path) -> None:
    initial = regular(source)
    deadline = time.monotonic() + 5

    def progress(_status: int, _remaining: int, _total: int) -> None:
        require(time.monotonic() < deadline, "database_timeout")

    with destination.open("xb"):
        pass
    with (
        closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=0.1)) as origin,
        closing(sqlite3.connect(destination, timeout=0.1)) as target,
    ):
        origin.backup(target, pages=256, progress=progress, sleep=0.05)
        _ = target.execute("PRAGMA journal_mode=DELETE")

    final = regular(source)
    require((initial.st_dev, initial.st_ino) == (final.st_dev, final.st_ino), "database_source_changed")
