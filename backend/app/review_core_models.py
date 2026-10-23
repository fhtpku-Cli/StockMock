"""正式结论的独立持久模型，不改变原账本 JSON。"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from app.models import Model

SubjectType = Literal["plan", "order"]
ReviewState = Literal["not_due", "pending", "reviewed", "needs_update"]
Market = Literal["CN", "US", "HK"]
ConclusionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class Coverage(Model):
    """用户实际读取的覆盖范围，不能由分页订单推算。"""

    plan_version: int | None = Field(default=None, ge=1, strict=True)
    filled_order_ids: list[str]
    due_review_date: date | None = None
    latest_conclusion_id: str | None = None


class ConclusionInput(Model):
    """成功请求按完整原始提交内容进行幂等重试。"""

    request_id: UUID
    round_id: UUID
    conclusion: ConclusionText
    deviation: OptionalText = ""
    next_action: OptionalText = ""
    coverage: Coverage


class Conclusion(Model):
    """追加保存的正式结论，原内容不覆盖。"""

    id: str
    round_id: str
    subject_type: SubjectType
    subject_id: str
    conclusion: str
    deviation: str
    next_action: str
    created_at: datetime
    coverage: Coverage
