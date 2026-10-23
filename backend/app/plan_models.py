"""交易计划、有效观察与复盘的 HTTP 边界。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.models import Currency, Journal, Model, Order, PlanSnapshot
from app.review_core_models import Coverage, Market, ReviewState, SubjectType


class PlanInput(Model):
    """价格仅为做多观察参考，不触发自动交易。"""

    symbol: str = Field(min_length=1, max_length=30)
    direction: Literal["watch", "buy", "sell"]
    status: Literal["draft", "active", "ended"] = "draft"
    reason: str = Field(default="", max_length=2000)
    target_price: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=4)
    stop_price: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=4)
    review_date: date | None = None
    invalidation: str = Field(default="", max_length=2000)


class PlanUpdate(PlanInput):
    """提交用户读取时的版本，阻止另一设备静默覆盖。"""

    version: int = Field(ge=1, strict=True)


class Monitoring(Model):
    """最近一次实际后台判断，缺失或不可靠行情明确不可判断。"""

    status: Literal["pending", "valid", "unavailable"] = "pending"
    message: str = "等待后台观察"
    checked_at: datetime | None = None
    market_time: datetime | None = None
    price: Decimal | None = None
    source: str | None = None


class Plan(PlanSnapshot):
    """可跨设备读取的计划及独立观察状态。"""

    monitoring: Monitoring = Field(default_factory=Monitoring)


class Page[T](Model):
    """有界分页以及完整数量。"""

    items: list[T]
    total: int
    offset: int
    limit: int


class Alert(Model):
    """每个计划版本、每个条件至多一条的实际观察提示。"""

    id: str
    round_id: str
    plan_id: str
    plan_version: int
    symbol: str
    condition: Literal["review_due", "target", "stop"]
    message: str
    created_at: datetime
    market_time: datetime | None = None
    price: Decimal | None = None
    source: str | None = None
    read_at: datetime | None = None


class AlertPage(Page[Alert]):
    """未读数量独立于当前分页。"""

    unread_count: int


class Review(Model):
    """计划或无计划旧单的复盘概要；不把部分卖出视为最终收益。"""

    id: str
    plan: Plan | None = None
    legacy_order: Order | None = None
    notes: list[Journal]
    order_count: int
    filled_count: int
    bought_quantity: int
    sold_quantity: int
    remaining_quantity: int
    account_quantity: int
    realized_pnl: Decimal | None
    currency: Currency
    status: Literal["pending", "reviewed", "not_due"]
    actual_status: str
    subject_type: SubjectType
    symbol: str
    market: Market | None = None
    created_at: datetime
    latest_record_at: datetime
    review_state: ReviewState
    update_reasons: list[str]
    coverage: Coverage
