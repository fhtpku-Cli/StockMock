"""当前轮次单只证券的建仓到清仓只读重放。"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, tzinfo
from decimal import Decimal
from typing import Annotated, Literal, final

from pydantic import AwareDatetime, PlainSerializer

from app.calendar import calendar
from app.models import Currency, Instrument, Model, Order, State
from app.personal_models import Adjustment
from app.storage import Store, read_rows

EventKind = Literal["first_buy", "add", "reduce", "close", "split", "dividend", "anomaly"]
SPLIT_BOUNDARY = "拆合股前数量核对不上，本段首买或清仓边界可能不准"


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


DecimalText = Annotated[Decimal, PlainSerializer(_decimal_text, return_type=str, when_used="json")]


class EpisodeEvent(Model):
    """单个成交或公司行动；金额在 JSON 边界使用十进制字符串。"""

    kind: EventKind
    at: AwareDatetime
    market_date: date
    currency: Currency
    quantity: int
    price: DecimalText | None
    fee: DecimalText | None
    position_after: int
    order_id: str | None
    ratio: str | None
    cash_per_share: DecimalText | None
    cash_amount: DecimalText | None
    payment_status: Literal["paid", "receivable"] | None
    note: str | None


class Episode(Model):
    """一次数量从零变正到归零的分段。"""

    index: int
    status: Literal["closed", "open"]
    opened_at: AwareDatetime
    closed_at: AwareDatetime | None
    opened_market_date: date
    closed_market_date: date | None
    holding_days: int
    buy_count: int
    sell_count: int
    peak_quantity: int
    boundary_note: str | None
    events: list[EpisodeEvent]


class PositionEpisodes(Model):
    """同一活动轮次快照的完整回看契约。"""

    symbol: str
    name: str
    currency: Currency
    round_id: str
    as_of: AwareDatetime
    market_today: date
    reconciled: bool
    reconcile_note: str | None
    episodes: list[Episode]
    outside_events: list[EpisodeEvent]


@dataclass(frozen=True, slots=True)
class _Input:
    at: datetime
    type_order: int
    secondary: datetime
    id: str
    order: Order | None = None
    adjustment: Adjustment | None = None
    missing: tuple[str, ...] = ()

    def sort_key(self) -> tuple[datetime, int, datetime, str]:
        """唯一、稳定的展示顺序，不把日级调整伪装成真实盘中时刻。"""
        return self.at, self.type_order, self.secondary, self.id


@dataclass(slots=True)
class _OpenEpisode:
    index: int
    opened_at: datetime
    opened_market_date: date
    events: list[EpisodeEvent] = field(default_factory=list)
    closed_at: datetime | None = None
    closed_market_date: date | None = None
    buy_count: int = 0
    sell_count: int = 0
    peak_quantity: int = 0
    peak_exact: bool = True
    notes: list[str] = field(default_factory=list)


def _aware(value: datetime) -> datetime:
    """旧数据若缺时区只按 UTC 解释，不让单条记录破坏整份回看。"""
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


def _midnight(day: date, zone: tzinfo) -> datetime:
    return datetime.combine(day, time.min, zone).astimezone(UTC)


def _missing_fields(order: Order) -> tuple[str, ...]:
    return tuple(name for name in ("filled_at", "fill_price", "fee") if getattr(order, name) is None)


def _inputs(orders: list[Order], adjustments: list[Adjustment], zone: tzinfo) -> list[_Input]:
    result: list[_Input] = []
    for order in orders:
        if order.status != "filled":
            continue
        missing = _missing_fields(order)
        at = order.filled_at or order.confirmed_at or order.created_at
        result.append(
            _Input(
                at=_aware(at),
                type_order=3 if missing else 2,
                secondary=_aware(order.confirmed_at or order.created_at),
                id=order.id,
                order=order,
                missing=missing,
            )
        )
    result.extend(
        _Input(
            at=_midnight(action.effective_date, zone),
            type_order=0 if action.kind == "split" else 1,
            secondary=_aware(action.created_at),
            id=action.id,
            adjustment=action,
        )
        for action in adjustments
    )
    return sorted(result, key=_Input.sort_key)


def _violates_registration(action: Adjustment, order: Order, zone: tzinfo) -> bool:
    if order.status != "filled" or order.filled_at is None:
        return False
    filled = _aware(order.filled_at)
    return filled.astimezone(zone).date() >= action.effective_date and filled < _aware(action.created_at)


def _joined(notes: list[str]) -> str | None:
    return "；".join(notes) if notes else None


@final
class _Replay:
    """可变重放状态仅存在于单次纯函数调用内。"""

    def __init__(
        self,
        orders: list[Order],
        adjustments: list[Adjustment],
        zone: tzinfo,
    ) -> None:
        self.zone = zone
        same_day = {day for day, count in Counter(a.effective_date for a in adjustments).items() if count > 1}
        self.adjustment_notes: dict[str, list[str]] = {}
        for action in adjustments:
            notes: list[str] = []
            if any(_violates_registration(action, order, self.zone) for order in orders):
                notes.append("生效日当天或之后存在早于登记的成交，本段首买或清仓边界可能不准")
            if action.effective_date in same_day:
                notes.append("同日多条调整按约定类型序、登记时间与编号稳定展示，本段首买或清仓边界可能不准")
            if notes:
                self.adjustment_notes[action.id] = notes
        self.inputs = _inputs(orders, adjustments, zone)
        self.pending_notes: dict[date, list[str]] = {}
        self.episodes: list[_OpenEpisode] = []
        self.outside: list[EpisodeEvent] = []
        self.current: _OpenEpisode | None = None
        self.position = 0
        self.issues: list[str] = []

    def issue(self, note: str) -> None:
        if note not in self.issues:
            self.issues.append(note)

    def mark(self, notes: list[str]) -> None:
        if notes:
            self.issue("部分公司行动的分段边界未知")
        if self.current is None:
            return
        for note in notes:
            if note not in self.current.notes:
                self.current.notes.append(note)

    def mark_adjustment(self, notes: list[str], effective_date: date) -> None:
        """调整先于首买时，把不确定性留给同市场日期的后续段。"""
        self.mark(notes)
        if self.current is not None:
            return
        pending = self.pending_notes.setdefault(effective_date, [])
        for note in notes:
            if note not in pending:
                pending.append(note)

    def rebase_peak(self, action: Adjustment, *, matched: bool) -> str | None:
        """正常调整换算历史峰值；边界未知时只保留新口径的实际记录。"""
        if self.current is None:
            return None
        if matched and self.current.peak_exact:
            scaled = self.current.peak_quantity * action.quantity_after
            if scaled % action.quantity_before == 0:
                self.current.peak_quantity = scaled // action.quantity_before
                return None
            note = "历史最大持仓无法按拆合股后口径精确换算"
            self.mark([note])
            self.current.peak_exact = False
            self.current.peak_quantity = self.position
            return note
        self.current.peak_exact = False
        self.current.peak_quantity = self.position
        return None

    def event(  # noqa: PLR0913 事件契约的可空字段在同一工厂显式展开。
        self,
        kind: EventKind,
        at: datetime,
        *,
        currency: Currency,
        quantity: int,
        price: Decimal | None = None,
        fee: Decimal | None = None,
        order_id: str | None = None,
        ratio: str | None = None,
        cash_per_share: Decimal | None = None,
        cash_amount: Decimal | None = None,
        payment_status: Literal["paid", "receivable"] | None = None,
        note: str | None = None,
    ) -> EpisodeEvent:
        return EpisodeEvent(
            kind=kind,
            at=at,
            market_date=at.astimezone(self.zone).date(),
            currency=currency,
            quantity=quantity,
            price=price,
            fee=fee,
            position_after=self.position,
            order_id=order_id,
            ratio=ratio,
            cash_per_share=cash_per_share,
            cash_amount=cash_amount,
            payment_status=payment_status,
            note=note,
        )

    def append(self, event: EpisodeEvent) -> None:
        if self.current is None:
            self.outside.append(event)
        else:
            self.current.events.append(event)

    def trade(self, data: _Input) -> None:
        order = data.order
        if order is None:
            return
        if data.missing:
            self.issue(f"已成交委托缺少字段：{'、'.join(data.missing)}")
            note = f"成交记录缺少字段：{'、'.join(data.missing)}；不参与数量变化"
            self.append(
                self.event(
                    "anomaly",
                    data.at,
                    currency=order.currency,
                    quantity=order.quantity,
                    price=order.fill_price,
                    fee=order.fee,
                    order_id=order.id,
                    note=note,
                )
            )
            return
        if order.filled_at is None or order.fill_price is None or order.fee is None:
            return
        if order.side == "buy":
            self.buy(data.at, order)
        else:
            self.sell(data.at, order)

    def buy(self, at: datetime, order: Order) -> None:
        if order.fill_price is None or order.fee is None:
            return
        if self.current is None:
            opened_date = at.astimezone(self.zone).date()
            self.current = _OpenEpisode(len(self.episodes) + 1, at, opened_date)
            for note in self.pending_notes.get(opened_date, []):
                if note not in self.current.notes:
                    self.current.notes.append(note)
            self.episodes.append(self.current)
            kind: EventKind = "first_buy"
            self.position = order.quantity
        else:
            kind = "add"
            self.position += order.quantity
        self.current.buy_count += 1
        self.current.peak_quantity = max(self.current.peak_quantity, self.position)
        self.append(
            self.event(
                kind,
                at,
                currency=order.currency,
                quantity=order.quantity,
                price=order.fill_price,
                fee=order.fee,
                order_id=order.id,
            )
        )

    def sell(self, at: datetime, order: Order) -> None:
        if order.fill_price is None or order.fee is None:
            return
        notes: list[str] = []
        if self.current is None:
            self.issue("存在卖出时没有进行中持仓段的异常记录")
            notes.append("卖出时没有进行中的持仓段")
            kind: EventKind = "close"
        else:
            self.current.sell_count += 1
            if order.quantity > self.position:
                self.issue("存在卖出数量超过重放持仓的异常记录")
                notes.append("卖出数量超过重放持仓，本段首买或清仓边界可能不准")
                self.mark(notes)
            kind = "close" if order.quantity >= self.position else "reduce"
        self.position = max(0, self.position - order.quantity)
        self.append(
            self.event(
                kind,
                at,
                currency=order.currency,
                quantity=order.quantity,
                price=order.fill_price,
                fee=order.fee,
                order_id=order.id,
                note=_joined(notes),
            )
        )
        if kind == "close" and self.current is not None:
            self.current.closed_at = at
            self.current.closed_market_date = at.astimezone(self.zone).date()
            self.current = None

    def split(self, data: _Input) -> None:
        action = data.adjustment
        if action is None:
            return
        notes = list(self.adjustment_notes.get(action.id, ()))
        self.mark_adjustment(notes, action.effective_date)
        matched = False
        if self.current is None:
            self.issue("存在没有对应持仓段的拆合股记录")
            notes.append("拆合股没有对应的进行中持仓段")
        elif self.position != action.quantity_before:
            self.issue("存在拆合股前数量核对不上")
            notes.append(SPLIT_BOUNDARY)
            self.mark([SPLIT_BOUNDARY])
            self.position = action.quantity_after
        else:
            matched = True
            self.position = action.quantity_after
        peak_note = self.rebase_peak(action, matched=matched)
        if peak_note is not None:
            notes.append(peak_note)
        ratio = f"{action.numerator}:{action.denominator}"
        self.append(
            self.event(
                "split",
                data.at,
                currency=action.currency,
                quantity=action.quantity_after,
                ratio=ratio,
                note=_joined(notes),
            )
        )
        if self.current is not None and self.position == 0:
            self.current.closed_at = data.at
            self.current.closed_market_date = data.at.astimezone(self.zone).date()
            self.current = None

    def dividend(self, data: _Input) -> None:
        action = data.adjustment
        if action is None:
            return
        notes = list(self.adjustment_notes.get(action.id, ()))
        self.mark_adjustment(notes, action.effective_date)
        cash_amount = action.cash_delta or action.receivable_amount
        self.append(
            self.event(
                "dividend",
                data.at,
                currency=action.currency,
                quantity=self.position if self.current is not None else 0,
                cash_per_share=action.cash_per_share,
                cash_amount=cash_amount,
                payment_status=action.payment_status,
                note=_joined(notes),
            )
        )

    def run(self) -> None:
        for data in self.inputs:
            if data.order is not None:
                self.trade(data)
            elif data.adjustment is not None and data.adjustment.kind == "split":
                self.split(data)
            else:
                self.dividend(data)


def replay(
    state: State,
    adjustments: list[Adjustment],
    item: Instrument,
    as_of: datetime,
) -> PositionEpisodes:
    """纯函数重放；不写库，也不修改传入状态或公司行动。"""
    as_of = _aware(as_of)
    zone = calendar(item.market).tz
    orders = [order for order in state.orders if order.symbol == item.symbol]
    actions = [action for action in adjustments if action.symbol == item.symbol]
    result = _Replay(orders, actions, zone)
    result.run()
    held = sum(lot.quantity for lot in state.lots if lot.symbol == item.symbol)
    if result.position != held:
        result.issue(f"与当前持仓核对不上：重放为 {result.position}，当前为 {held}")
    market_today = as_of.astimezone(zone).date()
    episodes: list[Episode] = []
    for saved in result.episodes:
        end_date = saved.closed_market_date or market_today
        episodes.append(
            Episode(
                index=saved.index,
                status="closed" if saved.closed_at is not None else "open",
                opened_at=saved.opened_at,
                closed_at=saved.closed_at,
                opened_market_date=saved.opened_market_date,
                closed_market_date=saved.closed_market_date,
                holding_days=max(0, (end_date - saved.opened_market_date).days),
                buy_count=saved.buy_count,
                sell_count=saved.sell_count,
                peak_quantity=saved.peak_quantity,
                boundary_note=_joined(saved.notes),
                events=saved.events,
            )
        )
    episodes.sort(key=lambda episode: (episode.opened_at, episode.index), reverse=True)
    return PositionEpisodes(
        symbol=item.symbol,
        name=item.name,
        currency=item.currency,
        round_id=state.id,
        as_of=as_of,
        market_today=market_today,
        reconciled=not result.issues,
        reconcile_note=_joined(result.issues),
        episodes=episodes,
        outside_events=result.outside,
    )


def position_episodes(store: Store, item: Instrument, as_of: datetime) -> PositionEpisodes:
    """在一个只读事务中取得活动轮次和该轮该证券的全部调整。"""
    with store.read_transaction() as connection:
        state = store.load(connection)
        adjustments = [
            Adjustment.model_validate_json(row[0])
            for row in read_rows(
                connection,
                "SELECT data FROM adjustments WHERE round_id=? AND symbol=?",
                (state.id, item.symbol),
            )
        ]
    return replay(state, adjustments, item, as_of)
