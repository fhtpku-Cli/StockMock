"""0.4.7 真实回环验收的隔离时钟与原始材料。"""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import ClassVar

from tests.test_analysis import evidence


class Clock:
    current: ClassVar[datetime] = datetime(2026, 10, 29, 2, tzinfo=UTC)
    calls: ClassVar[int] = 0

    @classmethod
    def now(cls, _tz: tzinfo | None = None) -> datetime:
        cls.calls += 1
        return cls.current


def dump(path: Path) -> str:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        return "\n".join(connection.iterdump())


def preserve(name: str, path: Path) -> None:
    target = os.getenv("STOCKMOCK_EVIDENCE")
    if target:
        with (
            closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(Path(target) / name)) as destination,
        ):
            source.backup(destination)


def receipt(name: str, value: str) -> None:
    evidence(name, value)
