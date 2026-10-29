"""交易边界与持久账本模型。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

Currency = Literal["CNY", "USD", "HKD"]


def percentage_text(value: Decimal) -> str:
    """只在JSON边界展开指数，不改变Decimal精度或舍入微小比例。"""
    return format(value, "f")


Percentage = Annotated[Decimal, PlainSerializer(percentage_text, return_type=str, when_used="json")]


class Model(BaseModel):
    """不可变的外部数据边界。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Instrument(Model):
    """标的最小交易规则与可交易状态。"""

    symbol: str
    name: str
    market: Literal["CN", "HK", "US"]
    currency: Currency
    kind: Literal["stock", "ETF"]
    lot_size: int
    tick_size: Decimal
    tradable: bool = False
    restriction: str = "行情时间与交易规则未完成核验，仅供观察"
    settlement_days: int = 0
    price_limit_ratio: Decimal | None = None
    min_buy_quantity: int | None = None
    quantity_step: int | None = None
    tick_rule: Literal["fixed", "HK", "US"] = "fixed"
    rule_source: str = ""
    verified_on: str = ""


class PlanSnapshot(Model):
    """委托受理时的完整计划内容，不随以后编辑变化。"""

    id: str
    round_id: str
    symbol: str
    currency: Currency
    direction: Literal["watch", "buy", "sell"]
    status: Literal["draft", "active", "ended"]
    reason: str
    target_price: Decimal | None
    stop_price: Decimal | None
    review_date: date | None
    invalidation: str
    version: int
    created_at: datetime
    updated_at: datetime


class OrderParameters(Model):
    """验证数量、价格与重复请求编号。"""

    plan_id: str | None = Field(default=None, max_length=36)
    plan_version: int | None = Field(default=None, ge=1, strict=True)
    request_id: str = Field(min_length=8, max_length=100)
    symbol: str = Field(max_length=30)
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"] = "limit"
    quantity: int = Field(gt=0, le=10000000, strict=True)
    limit_price: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=4)
    reason: str = Field(default="", max_length=2000)
    target_price: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=4)
    stop_price: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=4)


class OrderInput(OrderParameters):
    """请求可绑定草稿所属轮次，旧请求缺省不改变历史兼容。"""

    expected_round_id: str | None = Field(default=None, max_length=36)


class FillObservation(Model):
    """实际成交所采用的完整行情，金额以精确十进制持久化。"""

    symbol: str
    currency: Currency
    source: str
    interval_start: AwareDatetime
    market_time: AwareDatetime
    retrieved_at: AwareDatetime
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    volume: Decimal = Field(gt=0, allow_inf_nan=False)
    tradable: Literal[True]


class Order(OrderParameters):
    """持久化委托，保留市场时间和服务器确认时间。"""

    id: str
    plan_snapshot: PlanSnapshot | None = None
    price_bound: Decimal
    status: Literal["accepted", "cancel_pending", "filled", "cancelled", "expired", "terminated"] = "accepted"
    status_reason: str = "等待下单后的有效市场样本"
    created_at: datetime
    active_at: datetime
    expires_at: datetime
    cancel_at: datetime | None = None
    filled_at: datetime | None = None
    confirmed_at: datetime | None = None
    fill_price: Decimal | None = None
    fill_observation: FillObservation | None = None
    fee: Decimal | None = None
    cost_basis: Decimal | None = None
    realized_pnl: Decimal | None = None
    currency: Currency
    reserved_cny: Decimal = Decimal(0)
    reserved_foreign: Decimal = Decimal(0)
    fx_rate: Decimal = Decimal(1)
    fx_source: str = ""
    fx_time: datetime | None = None
    source: str
    last_sample_at: datetime | None = None
    daily_lower: Decimal | None = None
    daily_upper: Decimal | None = None


class Lot(Model):
    """持仓数量、成本与可卖时刻。"""

    symbol: str
    quantity: int
    cost: Decimal
    sellable_at: datetime


class Entry(Model):
    """不可覆盖的资金变动记录。"""

    id: str
    kind: str
    currency: Currency
    amount: Decimal
    created_at: datetime
    order_id: str | None = None
    note: str


class Journal(Model):
    """关联委托的复盘笔记。"""

    id: str
    order_id: str | None = None
    plan_id: str | None = None
    text: str
    created_at: datetime


class State(Model):
    """轮次聚合；集合仅在数据库事务内变更。"""

    id: str
    created_at: datetime
    archived_at: datetime | None = None
    cash: dict[Currency, Decimal] = Field(
        default_factory=lambda: {
            "CNY": Decimal("1000000.00"),
            "USD": Decimal(0),
            "HKD": Decimal(0),
        }
    )
    orders: list[Order] = Field(default_factory=list)
    lots: list[Lot] = Field(default_factory=list)
    ledger: list[Entry] = Field(default_factory=list)
    journal: list[Journal] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)


class CandidateInterval(Model):
    """按生效时刻推导的首个候选，不是预计成交或下一未处理区间。"""

    status: Literal["available", "none", "unknown"]
    start: datetime | None = None
    end: datetime | None = None
    reason: str


class WaitingView(Model):
    """活动委托的即时只读等待说明，不承诺来源覆盖全部成交。"""

    code: Literal["paused", "source_failing", "after_cutoff", "not_active", "awaiting_first", "checked_not_met"]
    message: str
    checked_through: datetime | None = None
    since: datetime | None = None


class OrderView(Order):
    """只读传输投影，新增解释不进入持久委托。"""

    first_candidate_interval: CandidateInterval
    waiting: WaitingView | None = None


class Preview(Model):
    """订单价格边界、费用与预留预览。"""

    active_at: datetime
    first_candidate_interval: CandidateInterval
    price_bound: Decimal
    estimated_fee: Decimal
    reserved_cny: Decimal
    reserved_foreign: Decimal
    fx_rate: Decimal | None
    fx_source: str | None
    fx_time: datetime | None
    expires_at: datetime
    warnings: list[str]


class CashView(Model):
    """各币种余额、占用与可用金额。"""

    currency: Currency
    balance: Decimal
    reserved: Decimal
    available: Decimal


class ActiveOrdersView(Model):
    """当前轮次真实活动状态计数。"""

    accepted_count: int = Field(ge=0, strict=True)
    cancel_pending_count: int = Field(ge=0, strict=True)


class SellabilityView(Model):
    """剩余批次的未来解锁摘要，不包含卖单占用。"""

    locked_quantity: int = Field(ge=0, strict=True)
    next_unlock_at: AwareDatetime | None
    next_unlock_quantity: int = Field(ge=0, strict=True)


class PositionView(Model):
    """持仓数量与明确标记的参考估值。"""

    market: Literal["CN", "HK", "US"] | None = None
    cost: Decimal | None = None
    pnl_percent: Percentage | None = None
    market_value_cny: Decimal | None = None
    weight_percent: Percentage | None = None
    price_time: datetime | None = None
    sellability: SellabilityView | None = None
    symbol: str
    name: str
    currency: Currency
    quantity: int
    sellable: int
    reserved: int
    average_cost: Decimal
    market_price: Decimal | None
    market_value: Decimal | None
    pnl: Decimal | None
    valuation_status: str


class WorkerView(Model):
    """后台处理最近运行状态。"""

    last_run: datetime | None = None
    status: str = "starting"


class Allocation(Model):
    """同一分母下的分类金额。"""

    key: str
    value_cny: Decimal | None
    weight_percent: Percentage | None


class Composition(Model):
    """现金包含占用，应收单列且不可花费。"""

    cash_cny: Decimal | None
    positions_cny: Decimal | None
    receivables_cny: Decimal | None
    cash_percent: Percentage | None
    positions_percent: Percentage | None
    receivables_percent: Percentage | None
    by_market: list[Allocation]
    by_currency: list[Allocation]
    price_times: dict[str, datetime]
    issues: list[str]


class HealthView(WorkerView):
    """兼容原撮合状态，同时暴露计划观察心跳。"""

    plan_worker: WorkerView


class Portfolio(Model):
    """个人活动轮次的资产视图。"""

    round_id: str
    active_orders: ActiveOrdersView | None = None
    composition: Composition | None = None
    initial_capital: Decimal = Decimal("1000000.00")
    total_assets_cny: Decimal | None
    pnl_cny: Decimal | None
    valuation_status: str
    cash: list[CashView]
    positions: list[PositionView]
    worker: WorkerView
    warnings: list[str]


class DomainError(Exception):
    """稳定业务错误码和 HTTP 状态。"""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        """保存依赖并初始化本实例的状态。"""
        super().__init__(message)
        self.code: str = code
        self.message: str = message
        self.status: int = status
