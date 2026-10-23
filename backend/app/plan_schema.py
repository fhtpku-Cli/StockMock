"""在旧账本之外增量创建计划表，重复启动无需重写账本。"""

import sqlite3


def initialize(connection: sqlite3.Connection) -> None:
    """复用现有启动事务，旧 JSON 字段由模型默认值兼容。"""
    statements = (
        """CREATE TABLE IF NOT EXISTS review_conclusions(
        id TEXT PRIMARY KEY, round_id TEXT NOT NULL, subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL, payload TEXT NOT NULL, data TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS conclusions_subject ON review_conclusions(round_id,subject_type,subject_id)",
        """CREATE TABLE IF NOT EXISTS plans(
        id TEXT PRIMARY KEY, round_id TEXT NOT NULL, symbol TEXT NOT NULL,
        status TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL)""",
        "CREATE UNIQUE INDEX IF NOT EXISTS one_active_plan ON plans(round_id,symbol) WHERE status='active'",
        "CREATE INDEX IF NOT EXISTS plans_round ON plans(round_id)",
        """CREATE TABLE IF NOT EXISTS plan_alerts(
        id TEXT PRIMARY KEY, round_id TEXT NOT NULL, plan_id TEXT NOT NULL,
        version INTEGER NOT NULL, condition TEXT NOT NULL, data TEXT NOT NULL,
        UNIQUE(plan_id,version,condition))""",
        "CREATE INDEX IF NOT EXISTS plan_alerts_round ON plan_alerts(round_id)",
        """CREATE TABLE IF NOT EXISTS plan_monitoring(
        plan_id TEXT PRIMARY KEY, version INTEGER NOT NULL,data TEXT NOT NULL)""",
    )
    for statement in statements:
        _ = connection.execute(statement)
