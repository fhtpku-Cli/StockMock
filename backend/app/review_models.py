"""复盘工作台与事实接口模型。"""

from datetime import datetime
from typing import Literal

from app.models import Journal, Model, Order
from app.plan_models import Alert, Page, Plan, Review
from app.review_core_models import Conclusion, Coverage, Market, SubjectType


class ReviewDetail(Model):
    """一致事实、完整委托和覆盖表单。"""

    round_id: str
    read_only: bool
    review: Review
    coverage: Coverage
    orders: list[Order]
    conclusions: list[Conclusion]


class ConclusionResult(Model):
    """重试返回原结论及原轮次的最新状态。"""

    conclusion: Conclusion
    round_id: str
    read_only: bool
    review: Review


class ReviewCounts(Model):
    """计数不受状态筛选和分页影响。"""

    pending: int
    needs_update: int
    reviewed: int
    not_due: int


class ReviewPage(Model):
    """稳定主体游标，同时返回当前完整计数。"""

    items: list[Review]
    total: int
    round_id: str
    read_only: bool
    counts: ReviewCounts
    next_cursor: str | None
    limit: int


class Fact(Model):
    """保留原始记录和真实系统时间，缺失时间不会被推断。"""

    id: str
    type: Literal[
        "order_submitted",
        "order_filled",
        "cancel_requested",
        "order_terminal",
        "plan_created",
        "plan_updated",
        "alert",
        "note",
        "conclusion",
    ]
    record_id: str
    occurred_at: datetime | None
    market_time: datetime | None = None
    order: Order | None = None
    alert: Alert | None = None
    note: Journal | None = None
    conclusion: Conclusion | None = None
    plan: Plan | None = None


class FactPage(Page[Fact]):
    """时间不完整的事实排在已知时间之后，仍可分页下载。"""

    round_id: str
    read_only: bool


class JournalItem(Journal):
    """只在传输边界扩展归属，原普通笔记不改写。"""

    symbol: str | None
    market: Market | None
    review_subject_type: SubjectType | None
    review_subject_id: str | None


class JournalPage(Page[JournalItem]):
    """笔记日期筛选与复盘最近记录日期独立。"""

    round_id: str
    read_only: bool
