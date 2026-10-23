"""只读资产分析的金额、日期和分页边界。"""

from datetime import date
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from app.models import Currency, Entry, Model
from app.personal_models import ValuationSnapshot
from app.plan_queries import PageQuery


class LedgerQuery(PageQuery):
    """日期含北京时间全天；追加边界仅影响列表。"""

    start_date: date | None = None
    end_date: date | None = None
    currency: Currency | None = None
    kind: str | None = None
    boundary: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def dates_ordered(self) -> Self:
        """拒绝倒置日期。"""
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise PydanticCustomError("date_order", "开始日期不能晚于结束日期")
        return self


class LedgerPage(Model):
    """边界为本轮追加序号，不保存额外查询快照。"""

    items: list[Entry]
    total: int
    offset: int
    limit: int
    boundary: int
    round_id: str


class CurrencyStatement(Model):
    """全轮核对与全类型日期区间计算。"""

    currency: Currency
    initial_cash: Decimal
    total_inflow: Decimal
    total_outflow: Decimal
    calculated_balance: Decimal
    ledger_balance: Decimal
    difference: Decimal
    opening_balance: Decimal
    period_inflow: Decimal
    period_outflow: Decimal
    period_net_change: Decimal
    closing_balance: Decimal
    selected_net_change: Decimal


class CashStatement(Model):
    """不同币种独立核对，类型不改变期初期末。"""

    round_id: str
    start_date: date | None
    end_date: date | None
    boundary: int
    currencies: list[CurrencyStatement]


class SnapshotQuery(Model):
    """快捷范围包含结束日，custom必须提供双端日期。"""

    range: Literal["all", "7d", "30d", "90d", "custom"] = "all"
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        """拒绝缺失或倒置端点。"""
        if self.range == "custom" and (self.start_date is None or self.end_date is None):
            raise PydanticCustomError("date_required", "自定范围需要开始和结束日期")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise PydanticCustomError("date_order", "开始日期不能晚于结束日期")
        return self


class SnapshotRange(Model):
    """端点永远是实际首末记录，不跳过无效端点。"""

    round_id: str
    start_date: date
    end_date: date
    items: list[ValuationSnapshot]
    recorded_days: int
    complete_days: int
    missing_days: int
    first: ValuationSnapshot | None
    last: ValuationSnapshot | None
    change_cny: Decimal | None
    continuous: bool
    reason: str
