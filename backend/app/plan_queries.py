"""有界分页的查询模型及统一切片。"""

from typing import Literal

from pydantic import Field

from app.models import Model
from app.plan_models import Page


class PageQuery(Model):
    """一次至多一百条，调用方可遍历直到 total。"""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class PlanQuery(PageQuery):
    """轮次计划的本地筛选。"""

    round_id: str | None = None
    symbol: str | None = None
    status: Literal["draft", "active", "ended"] | None = None


class AlertQuery(PageQuery):
    """同轮次提示筛选和未读数量。"""

    round_id: str | None = None
    symbol: str | None = None
    unread_only: bool = False


class ReviewQuery(PageQuery):
    """复盘资格按成交、到期和笔记计算。"""

    round_id: str | None = None
    status: Literal["all", "pending", "reviewed"] = "all"


def page[T](items: list[T], query: PageQuery) -> Page[T]:
    """保持输入顺序，仅截断传输范围。"""
    return Page(
        items=items[query.offset : query.offset + query.limit], total=len(items), offset=query.offset, limit=query.limit
    )
