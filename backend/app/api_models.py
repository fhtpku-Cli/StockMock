"""辅助 HTTP 边界模型。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.models import Currency, Entry, Journal, Model, Order


class SymbolInput(Model):
    """已支持证券的代码输入。"""

    symbol: str = Field(max_length=30)


class NoteInput(Model):
    """可选关联委托的笔记输入。"""

    text: str = Field(min_length=1, max_length=10000)
    order_id: str | None = None


class ConvertInput(Model):
    """将可用外币换回人民币的请求。"""

    request_id: str = Field(min_length=8, max_length=100)
    currency: Literal["USD", "HKD"]
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)


class ResetInput(Model):
    """明确确认重开练习的幂等请求。"""

    request_id: str = Field(min_length=8, max_length=100)
    confirmation: Literal["重新开始"]


class RoundView(Model):
    """练习轮次及归档时间。"""

    id: str
    created_at: datetime
    archived_at: datetime | None


class RoundResult(Model):
    """重开练习产生的轮次编号。"""

    round_id: str


class QuoteView(Model):
    """供应商估值快照及其原始市场时间。"""

    symbol: str
    price: Decimal
    tick_size: Decimal
    currency: Currency
    market_time: datetime
    retrieved_at: datetime
    source: str
    status: str
    message: str


class HistoryView(Model):
    """仅用于展示的未复权日线。"""

    time: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class Ok(Model):
    """操作成功响应。"""

    ok: bool = True


class RoundDetail(RoundView):
    """轮次只读详情；页面最多返回各集合最近五百项。"""

    cash: dict[Currency, Decimal]
    orders: list[Order] = Field(max_length=500)
    journal: list[Journal] = Field(max_length=500)
    ledger: list[Entry] = Field(max_length=500)
    truncated: bool
