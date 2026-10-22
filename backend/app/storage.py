"""单个人账户的 SQLite 原子聚合；BEGIN IMMEDIATE 串行化写入。"""

import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import uuid4

from pydantic import TypeAdapter

from app.models import DomainError, State


def read_rows(connection: sqlite3.Connection, sql: str, params: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """将 SQLite 动态结果一次解析成字符串行。"""
    return TypeAdapter(list[tuple[str, ...]]).validate_python(connection.execute(sql, params).fetchall())


@final
class Store:
    """个人单实例的持久事务入口。"""

    def __init__(self, path: Path) -> None:
        """保存依赖并初始化本实例的状态。"""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            _ = connection.execute(
                "CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY, active INTEGER NOT NULL, data TEXT NOT NULL)"
            )
            _ = connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON rounds(active) WHERE active=1")
            _ = connection.execute(
                """CREATE TABLE IF NOT EXISTS requests(
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, response TEXT NOT NULL)"""
            )
            _ = connection.execute(
                """CREATE TABLE IF NOT EXISTS snapshots(
                round_id TEXT NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(round_id,day))"""
            )
            _ = connection.execute(
                """CREATE TABLE IF NOT EXISTS adjustments(
                id TEXT PRIMARY KEY, round_id TEXT NOT NULL, symbol TEXT NOT NULL,
                kind TEXT NOT NULL, effective_date TEXT NOT NULL, data TEXT NOT NULL,
                UNIQUE(round_id,symbol,kind,effective_date))"""
            )
            _ = connection.execute(
                "CREATE TABLE IF NOT EXISTS observations(symbol TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            _ = connection.execute(
                "CREATE TABLE IF NOT EXISTS activated_instruments(symbol TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            if connection.execute("SELECT id FROM rounds WHERE active=1").fetchone() is None:
                self.save(connection, State(id=str(uuid4()), created_at=datetime.now(UTC)))

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """串行化写事务并确保提交、回滚和连接释放。"""
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            _ = connection.execute("PRAGMA journal_mode=WAL")
            with connection:
                _ = connection.execute("BEGIN IMMEDIATE")
                yield connection

    def load(self, connection: sqlite3.Connection) -> State:
        """读取唯一活动轮次，拒绝损坏的数据库状态。"""
        rows = read_rows(connection, "SELECT data FROM rounds WHERE active=1")
        if not rows:
            raise DomainError("missing_round", "账户数据库缺少活动轮次", 500)
        return State.model_validate_json(rows[0][0])

    def save(self, connection: sqlite3.Connection, state: State) -> None:
        """在已有事务中保存轮次聚合。"""
        _ = connection.execute(
            "INSERT INTO rounds VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET active=excluded.active,data=excluded.data",
            (state.id, int(state.archived_at is None), state.model_dump_json()),
        )

    def read(self) -> State:
        """取得一次一致的活动轮次快照。"""
        with self.transaction() as connection:
            return self.load(connection)

    def read_round(self, round_id: str) -> State:
        """按编号读取当前或归档轮次，不修改历史内容。"""
        with self.transaction() as connection:
            rows = read_rows(connection, "SELECT data FROM rounds WHERE id=?", (round_id,))
            if not rows:
                raise DomainError("round_missing", "练习轮次不存在", 404)
            return State.model_validate_json(rows[0][0])
