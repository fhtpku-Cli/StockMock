"""建仓到清仓重放的受控账本与真实 HTTP 契约测试。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal, TypedDict

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue, TypeAdapter

from app.catalog import instrument
from app.engine import Engine
from app.episodes import PositionEpisodes, replay
from app.main import create_app
from app.models import Currency, Lot, Order, State
from app.personal_models import Adjustment
from app.storage import Store
from tests.test_api import PASSWORD

BASE = datetime(2026, 10, 19, 1, 30, tzinfo=UTC)
ZERO: Final = Decimal(0)


class EpisodePayload(TypedDict):
    events: list[dict[str, JsonValue]]


class ResponsePayload(TypedDict):
    round_id: str
    episodes: list[EpisodePayload]


def order(  # noqa: PLR0913 受控成交工厂显式提供各排序与异常字段。
    order_id: str,
    side: Literal["buy", "sell"],
    at: datetime,
    *,
    symbol: str = "600519.SS",
    quantity: int = 100,
    confirmed_at: datetime | None = None,
    missing: tuple[str, ...] = (),
) -> Order:
    item = instrument(symbol)
    return Order(
        id=order_id,
        request_id=f"request-{order_id}",
        symbol=symbol,
        side=side,
        quantity=quantity,
        price_bound=Decimal("10.50"),
        status="filled",
        created_at=at - timedelta(hours=1),
        active_at=at - timedelta(hours=1),
        expires_at=at + timedelta(days=1),
        confirmed_at=confirmed_at or at,
        filled_at=None if "filled_at" in missing else at,
        fill_price=None if "fill_price" in missing else Decimal("10.50"),
        fee=None if "fee" in missing else Decimal("0.50"),
        currency=item.currency,
        source="controlled-test",
    )


def adjustment(  # noqa: PLR0913 公司行动工厂需要覆盖比例、现金和边界异常。
    action_id: str,
    state: State,
    effective_at: datetime,
    *,
    kind: Literal["split", "dividend"] = "split",
    before: int = 100,
    after: int = 200,
    created_at: datetime | None = None,
    payment_status: Literal["paid", "receivable"] = "paid",
    cash_delta: Decimal = ZERO,
    receivable_amount: Decimal = ZERO,
    ratio: tuple[int, int] = (2, 1),
) -> Adjustment:
    created = created_at or effective_at
    split = kind == "split"
    return Adjustment(
        id=action_id,
        request_id=f"adjustment-{action_id}",
        round_id=state.id,
        symbol="600519.SS",
        kind=kind,
        effective_date=effective_at.date(),
        expected_quantity=before,
        note="已核对受控公司行动",
        cash_per_share=None if split else Decimal("1.25"),
        payment_status=payment_status,
        numerator=ratio[0] if split else None,
        denominator=ratio[1] if split else None,
        created_at=created,
        quantity_before=before,
        quantity_after=after,
        cash_delta=cash_delta,
        receivable_amount=receivable_amount,
        currency="CNY",
        cost_before=Decimal(0),
        cost_after=Decimal(0),
        paid_at=created if payment_status == "paid" else None,
    )


def state_with(orders: list[Order], *, lots: list[Lot] | None = None) -> State:
    return State(id="round-episodes", created_at=BASE, orders=orders, lots=lots or [])


def test_single_closed_episode_with_partial_sells_and_exact_amounts() -> None:
    # Given: 两次买入和两次卖出，第一次卖出后仍有持仓。
    orders = [
        order("buy-1", "buy", BASE),
        order("buy-2", "buy", BASE + timedelta(days=1), quantity=50),
        order("sell-1", "sell", BASE + timedelta(days=2), quantity=40),
        order("sell-2", "sell", BASE + timedelta(days=3), quantity=110),
    ]
    # When: 纯函数重放。
    result = replay(state_with(orders), [], instrument("600519.SS"), BASE + timedelta(days=4))
    # Then: 只有一段，首买、加仓、减仓和清仓数量及持有天数准确。
    assert result.reconciled
    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode.status == "closed"
    assert [event.kind for event in episode.events] == ["first_buy", "add", "reduce", "close"]
    assert [event.position_after for event in episode.events] == [100, 150, 110, 0]
    assert (episode.buy_count, episode.sell_count, episode.peak_quantity) == (2, 2, 150)
    assert episode.holding_days == 3
    raw = episode.events[0].model_dump(mode="json")
    assert raw["price"] == "10.50"
    assert raw["fee"] == "0.50"
    assert raw["currency"] == "CNY"


def test_two_episodes_same_day_close_and_open_days_in_market_timezone() -> None:
    # Given: 第一段同日清仓，第二段仍持有，服务端时刻跨市场日期。
    orders = [
        order("old-buy", "buy", BASE),
        order("old-sell", "sell", BASE + timedelta(hours=2)),
        order("new-buy", "buy", datetime(2026, 10, 26, 16, 30, tzinfo=UTC), quantity=20),
    ]
    lots = [Lot(symbol="600519.SS", quantity=20, cost=Decimal(200), sellable_at=BASE)]
    # When: 以北京 10/28 00:30 为计算时刻。
    result = replay(
        state_with(orders, lots=lots),
        [],
        instrument("600519.SS"),
        datetime(2026, 10, 27, 16, 30, tzinfo=UTC),
    )
    # Then: 最新段在前，序号仍按发生顺序；同日为 0，进行中含当天自然日差。
    assert [episode.index for episode in result.episodes] == [2, 1]
    latest, previous = result.episodes
    assert latest.status == "open"
    assert latest.opened_market_date.isoformat() == "2026-10-27"
    assert latest.holding_days == 1
    assert previous.holding_days == 0
    assert previous.closed_at is not None


def test_trade_sort_is_stable_by_confirmation_then_order_id() -> None:
    # Given: 两笔同刻成交的输入顺序与唯一排序键相反。
    at = BASE + timedelta(days=1)
    orders = [
        order("z-last", "buy", at, quantity=20, confirmed_at=at + timedelta(seconds=2)),
        order("b-id", "buy", at, quantity=30, confirmed_at=at + timedelta(seconds=1)),
        order("a-id", "buy", at, quantity=40, confirmed_at=at + timedelta(seconds=1)),
        order("first", "buy", BASE),
    ]
    lots = [Lot(symbol="600519.SS", quantity=190, cost=Decimal(1900), sellable_at=BASE)]
    result = replay(state_with(orders, lots=lots), [], instrument("600519.SS"), at)
    # Then: 先 confirmed_at、再 order_id，不受 State 中列表顺序影响。
    assert [event.order_id for event in result.episodes[0].events] == ["first", "a-id", "b-id", "z-last"]


def test_split_changes_position_and_peak_without_rewriting_trade_price() -> None:
    # Given: 100 股拆为 200 股后减仓 50 股。
    state = state_with([order("buy", "buy", BASE), order("sell", "sell", BASE + timedelta(days=2), quantity=50)])
    split = adjustment("split", state, BASE + timedelta(days=1))
    lots = [Lot(symbol="600519.SS", quantity=150, cost=Decimal(1000), sellable_at=BASE)]
    result = replay(state.model_copy(update={"lots": lots}), [split], instrument("600519.SS"), BASE + timedelta(days=3))
    # Then: 拆股事件展示记录比例与调整后份额，原成交价不变。
    episode = result.episodes[0]
    split_event = episode.events[1]
    assert split_event.kind == "split"
    assert split_event.ratio == "2:1"
    assert (split_event.quantity, split_event.position_after) == (200, 200)
    assert split_event.price is None
    assert episode.events[0].price == Decimal("10.50")
    assert episode.peak_quantity == 200
    assert episode.events[-1].position_after == 150
    assert result.reconciled


def test_reverse_split_rebases_peak_to_post_split_units() -> None:
    # Given: 首买 200 股后按 1:2 合股为 100 股。
    state = state_with(
        [order("buy", "buy", BASE, quantity=200)],
        lots=[Lot(symbol="600519.SS", quantity=100, cost=Decimal(1000), sellable_at=BASE)],
    )
    split = adjustment("reverse", state, BASE + timedelta(days=1), before=200, after=100, ratio=(1, 2))
    result = replay(state, [split], instrument("600519.SS"), BASE + timedelta(days=2))
    # Then: 峰值不再把合股前 200 与合股后 100 混用。
    assert result.episodes[0].peak_quantity == 100
    assert result.reconciled


def test_continuous_splits_rebase_peak_through_each_latest_unit() -> None:
    # Given: 400 股先 2:1 拆为 800，卖出 400 后再按 1:4 合为 100。
    orders = [order("buy", "buy", BASE, quantity=400), order("sell", "sell", BASE + timedelta(days=2), quantity=400)]
    state = state_with(
        orders,
        lots=[Lot(symbol="600519.SS", quantity=100, cost=Decimal(1000), sellable_at=BASE)],
    )
    first = adjustment("split-first", state, BASE + timedelta(days=1), before=400, after=800)
    second = adjustment("split-second", state, BASE + timedelta(days=3), before=400, after=100, ratio=(1, 4))
    result = replay(state, [first, second], instrument("600519.SS"), BASE + timedelta(days=4))
    # Then: 历史峰值 400×2÷4=200，与最终份额口径一致。
    assert result.episodes[0].peak_quantity == 200
    assert result.reconciled


def test_split_mismatch_reanchors_to_quantity_after() -> None:
    # Given: 重放 100 股，但拆股记录核对前为 90、后为 180。
    state = state_with([order("buy", "buy", BASE), order("sell", "sell", BASE + timedelta(days=2), quantity=180)])
    split = adjustment("split", state, BASE + timedelta(days=1), before=90, after=180)
    result = replay(state, [split], instrument("600519.SS"), BASE + timedelta(days=3))
    # Then: 明确边界未知，之后以 180 为锚继续并可正常清仓。
    episode = result.episodes[0]
    assert episode.events[1].position_after == 180
    assert episode.events[2].position_after == 0
    assert episode.boundary_note is not None
    assert "拆合股前数量核对不上" in episode.boundary_note
    assert not result.reconciled
    assert result.reconcile_note is not None
    assert "拆合股" in result.reconcile_note


def test_zero_split_closes_marked_episode_and_new_episode_does_not_inherit() -> None:
    # Given: 异常拆合股将数量锚定为零，之后重新买入。
    orders = [order("buy-old", "buy", BASE), order("buy-new", "buy", BASE + timedelta(days=2), quantity=20)]
    state = state_with(
        orders,
        lots=[Lot(symbol="600519.SS", quantity=20, cost=Decimal(200), sellable_at=BASE)],
    )
    split = adjustment("split-zero", state, BASE + timedelta(days=1), before=90, after=0)
    result = replay(state, [split], instrument("600519.SS"), BASE + timedelta(days=3))
    # Then: 原段在拆股事件结束，新段从实际首买开始且不继承标注。
    latest, previous = result.episodes
    assert previous.status == "closed"
    assert previous.closed_at == previous.events[-1].at
    assert previous.boundary_note is not None
    assert latest.status == "open"
    assert latest.boundary_note is None
    assert latest.events[0].kind == "first_buy"
    assert latest.events[0].position_after == 20


def test_s1_trade_before_adjustment_registration_marks_unknown_boundary() -> None:
    # Given: 历史坏数据在生效日有早于调整登记时刻的成交。
    trade_at = BASE + timedelta(days=1, hours=2)
    orders = [order("buy", "buy", BASE), order("later", "buy", trade_at, quantity=50)]
    state = state_with(
        orders,
        lots=[Lot(symbol="600519.SS", quantity=250, cost=Decimal(2500), sellable_at=BASE)],
    )
    split = adjustment(
        "split-late",
        state,
        BASE + timedelta(days=1),
        after=200,
        created_at=BASE + timedelta(days=1, hours=12),
    )
    result = replay(state, [split], instrument("600519.SS"), trade_at + timedelta(days=1))
    # Then: 不推断真实盘中先后，但保留稳定时间线并标注边界。
    episode = result.episodes[0]
    assert [event.kind for event in episode.events] == ["first_buy", "split", "add"]
    assert episode.boundary_note is not None
    assert "早于登记" in episode.boundary_note
    assert not result.reconciled


def test_s1_same_day_adjustments_mark_boundary_and_use_type_order() -> None:
    # Given: 同一证券同一生效日同时有拆股和股息。
    state = state_with(
        [order("buy", "buy", BASE)],
        lots=[Lot(symbol="600519.SS", quantity=200, cost=Decimal(1000), sellable_at=BASE)],
    )
    effective = BASE + timedelta(days=1)
    split = adjustment("split", state, effective, created_at=effective + timedelta(minutes=2))
    dividend = adjustment(
        "dividend",
        state,
        effective,
        kind="dividend",
        before=200,
        after=200,
        created_at=effective + timedelta(minutes=1),
        cash_delta=Decimal("250.00"),
    )
    result = replay(state, [dividend, split], instrument("600519.SS"), effective + timedelta(days=1))
    # Then: 最终类型序为拆股、股息；同日不确定性写入段级标记。
    episode = result.episodes[0]
    assert [event.kind for event in episode.events] == ["first_buy", "split", "dividend"]
    assert episode.events[-1].quantity == 200
    assert episode.boundary_note is not None
    assert "同日多条调整" in episode.boundary_note
    assert not result.reconciled


@pytest.mark.parametrize("missing", [("filled_at",), ("fill_price",), ("fee",)])
def test_s2_bad_filled_order_is_anomaly_without_position_change(missing: tuple[str, ...]) -> None:
    # Given: 一笔字段缺失的 filled 委托及一笔正常首买。
    bad = order("bad", "sell", BASE + timedelta(days=1), quantity=40, missing=missing)
    state = state_with(
        [bad, order("buy", "buy", BASE)],
        lots=[Lot(symbol="600519.SS", quantity=100, cost=Decimal(1000), sellable_at=BASE)],
    )
    result = replay(state, [], instrument("600519.SS"), BASE + timedelta(days=2))
    # Then: 坏记录仍带委托号列出，但不减仓，顶部明确缺失字段。
    episode = result.episodes[0]
    anomaly = episode.events[-1]
    assert anomaly.kind == "anomaly"
    assert anomaly.order_id == "bad"
    assert anomaly.position_after == 100
    assert anomaly.note is not None
    assert missing[0] in anomaly.note
    assert not result.reconciled
    assert result.reconcile_note is not None
    assert missing[0] in result.reconcile_note


def test_s2_anomaly_sorts_after_normal_trade_at_same_time() -> None:
    # Given: 异常记录的确认时间更早，但最终类型序要求正常成交在前。
    at = BASE + timedelta(days=1)
    orders = [
        order("anomaly", "buy", at, quantity=40, confirmed_at=at - timedelta(seconds=1), missing=("fee",)),
        order("normal", "buy", at, quantity=50),
        order("first", "buy", BASE),
    ]
    state = state_with(
        orders,
        lots=[Lot(symbol="600519.SS", quantity=150, cost=Decimal(1500), sellable_at=BASE)],
    )
    result = replay(state, [], instrument("600519.SS"), at)
    assert [event.kind for event in result.episodes[0].events] == ["first_buy", "add", "anomaly"]


@pytest.mark.parametrize(
    ("status", "cash", "receivable"),
    [("paid", Decimal("125.00"), Decimal(0)), ("receivable", Decimal(0), Decimal("125.00"))],
)
def test_dividend_belongs_to_open_episode_with_cash_status(
    status: Literal["paid", "receivable"], cash: Decimal, receivable: Decimal
) -> None:
    # Given: 段内一笔已到账或应收股息。
    state = state_with(
        [order("buy", "buy", BASE)],
        lots=[Lot(symbol="600519.SS", quantity=100, cost=Decimal(1000), sellable_at=BASE)],
    )
    dividend = adjustment(
        "dividend",
        state,
        BASE + timedelta(days=1),
        kind="dividend",
        after=100,
        payment_status=status,
        cash_delta=cash,
        receivable_amount=receivable,
    )
    result = replay(state, [dividend], instrument("600519.SS"), BASE + timedelta(days=2))
    event = result.episodes[0].events[-1]
    # Then: 股息不改变持仓，每股税前金额、现金或应收与状态完整。
    assert event.kind == "dividend"
    assert (event.quantity, event.position_after) == (100, 100)
    assert event.cash_per_share == Decimal("1.25")
    assert event.cash_amount == Decimal("125.00")
    assert event.payment_status == status


def test_dividend_outside_any_episode_is_listed_separately() -> None:
    # Given: 清仓后才出现的段外股息。
    state = state_with([order("buy", "buy", BASE), order("sell", "sell", BASE + timedelta(days=1))])
    dividend = adjustment(
        "outside",
        state,
        BASE + timedelta(days=2),
        kind="dividend",
        after=100,
        cash_delta=Decimal("125.00"),
    )
    result = replay(state, [dividend], instrument("600519.SS"), BASE + timedelta(days=3))
    assert result.episodes[0].events[-1].kind != "dividend"
    assert len(result.outside_events) == 1
    assert result.outside_events[0].kind == "dividend"
    assert result.outside_events[0].position_after == 0


def test_terminal_position_mismatch_is_visible() -> None:
    # Given: 重放已归零，但当前 lots 仍有 10 股。
    state = state_with(
        [order("buy", "buy", BASE), order("sell", "sell", BASE + timedelta(days=1))],
        lots=[Lot(symbol="600519.SS", quantity=10, cost=Decimal(100), sellable_at=BASE)],
    )
    result = replay(state, [], instrument("600519.SS"), BASE + timedelta(days=2))
    assert not result.reconciled
    assert result.reconcile_note is not None
    assert "与当前持仓核对不上" in result.reconcile_note
    assert len(result.episodes) == 1


def test_oversell_closes_at_zero_and_marks_instead_of_raising() -> None:
    # Given: 受控坏数据卖出 150 股，超过重放持仓 100 股。
    state = state_with([order("buy", "buy", BASE), order("sell", "sell", BASE + timedelta(days=1), quantity=150)])
    result = replay(state, [], instrument("600519.SS"), BASE + timedelta(days=2))
    episode = result.episodes[0]
    assert episode.events[-1].kind == "close"
    assert episode.events[-1].position_after == 0
    assert episode.boundary_note is not None
    assert "超过重放持仓" in episode.boundary_note
    assert not result.reconciled


@pytest.mark.parametrize(
    ("symbol", "currency"),
    [("600519.SS", "CNY"), ("0700.HK", "HKD"), ("AAPL", "USD")],
)
def test_each_market_uses_its_own_event_currency_and_decimal_json(symbol: str, currency: Currency) -> None:
    # Given / When: 分别重放 A、港、美股的一笔首买。
    state = state_with(
        [order("buy", "buy", BASE, symbol=symbol)],
        lots=[Lot(symbol=symbol, quantity=100, cost=Decimal(1000), sellable_at=BASE)],
    )
    result = replay(state, [], instrument(symbol), BASE + timedelta(days=1))
    raw = result.model_dump(mode="json")
    # Then: 币种不硬编码，所有价格和费用以字符串输出。
    assert raw["currency"] == raw["episodes"][0]["events"][0]["currency"] == currency
    assert raw["episodes"][0]["events"][0]["price"] == "10.50"
    assert raw["episodes"][0]["events"][0]["fee"] == "0.50"


def save_snapshot(engine: Engine, state: State, actions: list[Adjustment]) -> None:
    with engine.store.transaction() as connection:
        saved = engine.store.load(connection)
        saved.orders[:] = state.orders
        saved.lots[:] = state.lots
        engine.store.save(connection, saved)
        for action in actions:
            _ = connection.execute(
                "INSERT INTO adjustments VALUES(?,?,?,?,?,?)",
                (
                    action.id,
                    saved.id,
                    action.symbol,
                    action.kind,
                    str(action.effective_date),
                    action.model_copy(update={"round_id": saved.id}).model_dump_json(),
                ),
            )


def test_http_contract_empty_unknown_and_authentication(engine: Engine, api: TestClient) -> None:
    # Given: 当前轮次没有成交与调整。
    empty = api.get("/api/positions/600519.SS/episodes")
    # Then: 已知证券返回完整空结果，未知证券 404。
    assert empty.status_code == 200
    view = PositionEpisodes.model_validate_json(empty.content)
    assert view.episodes == view.outside_events == []
    assert view.round_id == engine.store.read().id
    assert view.as_of == datetime(2026, 10, 22, 2, 0, tzinfo=UTC)
    assert view.market_today.isoformat() == "2026-10-22"
    assert view.reconciled
    unknown = api.get("/api/positions/UNKNOWN/episodes")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "unknown_symbol"
    # When: 另一个客户端未登录。
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as anonymous:
        # Then: 路由与同文件其他个人接口一样要求鉴权。
        assert anonymous.get("/api/positions/600519.SS/episodes").status_code == 401


def test_http_reads_state_and_adjustments_in_one_snapshot(
    engine: Engine, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 当前活动轮次有一笔成交与一次拆股；禁止绕道 Store.read 再读第二次状态。
    current = engine.store.read()
    state = state_with(
        [order("buy", "buy", BASE)],
        lots=[Lot(symbol="600519.SS", quantity=200, cost=Decimal(1000), sellable_at=BASE)],
    )
    split = adjustment("split", state, BASE + timedelta(days=1))
    save_snapshot(engine, state, [split])

    def separate_read(_store: Store) -> State:
        raise AssertionError

    monkeypatch.setattr(Store, "read", separate_read)
    response = api.get("/api/positions/600519.SS/episodes")
    # Then: 同一活动轮次快照包含两类记录，且所有契约键均返回。
    assert response.status_code == 200
    raw = TypeAdapter(ResponsePayload).validate_json(response.content)
    assert raw["round_id"] == current.id
    assert [event["kind"] for event in raw["episodes"][0]["events"]] == ["first_buy", "split"]
    assert set(raw["episodes"][0]["events"][0]) == {
        "kind",
        "at",
        "market_date",
        "currency",
        "quantity",
        "price",
        "fee",
        "position_after",
        "order_id",
        "ratio",
        "cash_per_share",
        "cash_amount",
        "payment_status",
        "note",
    }
    assert raw["episodes"][0]["events"][0]["price"] == "10.50"
