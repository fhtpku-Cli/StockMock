"""跨 HTTP 与交易场景共享隔离数据库 fixture。"""

from tests.test_engine import engine

__all__ = ["engine"]
