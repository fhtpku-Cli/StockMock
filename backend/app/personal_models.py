"""每日观察与人工公司行动的输入和审计边界。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.models import Currency, Model


class SnapshotFailure(Model):
    """首次不完整观察的原始时间与原因，恢复时仍保留。"""

    captured_at: datetime
    status: Literal["stale", "missing"]
    issues: list[str]


class ValuationSnapshot(Model):
    """当天首次完整观察；不补算过去缺失日。"""

    id: str
    round_id: str
    day: date
    captured_at: datetime
    total_assets_cny: Decimal | None
    pnl_cny: Decimal | None
    status: Literal["complete", "stale", "missing"]
    issues: list[str]
    price_times: dict[str, datetime]
    first_failure: SnapshotFailure | None = None


class AdjustmentInput(Model):
    """本人核对当前份额、日期与来源后的公司行动。"""

    request_id: str = Field(min_length=8, max_length=100)
    round_id: str
    symbol: str = Field(max_length=30)
    kind: Literal["dividend", "split"]
    effective_date: date
    expected_quantity: int = Field(gt=0, le=10000000, strict=True)
    note: str = Field(min_length=5, max_length=2000)
    cash_per_share: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=6)
    payment_status: Literal["receivable", "paid"] = "paid"
    numerator: int | None = Field(default=None, gt=0, le=10000, strict=True)
    denominator: int | None = Field(default=None, gt=0, le=10000, strict=True)


class Adjustment(AdjustmentInput):
    """保留原始依据与前后份额、成本及现金差异。"""

    id: str
    created_at: datetime
    quantity_before: int
    quantity_after: int
    cash_delta: Decimal
    receivable_amount: Decimal
    currency: Currency
    cost_before: Decimal
    cost_after: Decimal
    paid_at: datetime | None = None


class PaymentInput(Model):
    """确认已实际收到此前登记的应收股息。"""

    request_id: str = Field(min_length=8, max_length=100)
    round_id: str


class Sizing(Model):
    """以服务端精确金额计算的可委托数量。"""

    max_quantity: int
    suggested_quantity: int
    quantity_step: int
    minimum_quantity: int
