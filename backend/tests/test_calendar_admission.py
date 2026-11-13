"""真实日历与 SQLite 的跨年准入回归；行情和日期均受控。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal, assert_never

import pytest

from app import trading_routes
from app.accounting import available
from app.engine import Engine
from app.main import create_app
from app.models import DomainError
from app.sizing import size_order
from tests.market_session_support import Clock, dump, receipt
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import ControlledProvider, request, sample

NORMAL: Final = datetime(2026, 12, 30, 1, 35, tzinfo=UTC)
PREOPEN: Final = datetime(2026, 12, 30, 7, 1, tzinfo=UTC)
LAST: Final = datetime(2026, 12, 31, 1, 35, tzinfo=UTC)
UNKNOWN: Final = datetime(2027, 1, 4, 1, 35, tzinfo=UTC)


def configure(engine: Engine, now: datetime) -> None:
    engine.clock = lambda: now
    engine.provider = ControlledProvider(
        quote_time=now - timedelta(minutes=1),
        history_day=now.date() if now.hour >= 7 else (now - timedelta(days=1)).date(),
    )


@pytest.mark.parametrize("now", [PREOPEN, LAST, UNKNOWN], ids=["preopen-dec31", "intraday-dec31", "unknown-jan4"])
@pytest.mark.parametrize("action", ["preview", "size", "place"])
def test_reject_without_writes_when_settlement_calendar_missing(
    engine: Engine, now: datetime, action: Literal["preview", "size", "place"]
) -> None:
    # Given: 独立 SQLite 和真实 XSHG 日历，前收与样本时刻有效。
    configure(engine, now)
    before = dump(engine.store.path)
    body = request()
    # When: 从三个公开引擎入口尝试买入。
    match action:
        case "preview":
            with pytest.raises(DomainError) as error:
                _ = engine.preview(body, now)
        case "size":
            with pytest.raises(DomainError) as error:
                _ = size_order(engine, body, now, 100)
        case "place":
            with pytest.raises(DomainError) as error:
                _ = engine.place(body, now)
        case _:
            assert_never(action)
    # Then: 稳定错误码；全部表逐行相同，包含订单、请求、持仓和现金。
    assert error.value.code == "calendar_unavailable"
    assert dump(engine.store.path) == before
    assert available(engine.store.read(), "CNY") == Decimal(1000000)


def test_buy_and_fill_when_next_session_is_covered(engine: Engine) -> None:
    # Given: 12 月 30 日仍能算出次日可卖时刻。
    configure(engine, NORMAL)
    body = request()
    assert engine.preview(body, NORMAL).active_at == NORMAL
    assert size_order(engine, body, NORMAL, 100).max_quantity > 0
    order = engine.place(body, NORMAL)
    quote = sample(engine, order)
    # When: 完整区间触发真实成交记账。
    engine.process(order.symbol, (quote,), quote.market_time + timedelta(seconds=1))
    # Then: 买单成交，持仓次日开盘解锁。
    state = engine.store.read()
    assert state.orders[0].status == "filled"
    assert state.lots[0].sellable_at == datetime(2026, 12, 31, 1, 30, tzinfo=UTC)
    assert len(state.ledger) == 2


def test_sell_when_existing_lot_is_available_on_last_session(engine: Engine) -> None:
    # Given: 真实成交形成的已有持仓已到保存的可卖时刻。
    configure(engine, NORMAL)
    buy = engine.place(request(), NORMAL)
    quote = sample(engine, buy)
    engine.process(buy.symbol, (quote,), quote.market_time + timedelta(seconds=1))
    original_lots = engine.store.read().lots
    configure(engine, LAST)
    body = request().model_copy(update={"side": "sell"})
    assert size_order(engine, body, LAST, 100).max_quantity == 100
    assert engine.preview(body, LAST).reserved_cny == 0
    # When: 最后覆盖日提交卖出。
    order = engine.place(body, LAST)
    # Then: 正常受理，已有 Lot 的解锁时间不重算。
    assert order.status == "accepted"
    assert engine.store.read().lots == original_lots


def test_t0_buy_when_cn_settlement_calendar_is_exhausted(engine: Engine) -> None:
    # Given: 实际 T+0 美股规则；已有外币避免无关汇率时效。
    now = datetime(2026, 12, 31, 15, 35, tzinfo=UTC)
    configure(engine, now)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(20000)
        engine.store.save(connection, state)
    body = request().model_copy(update={"symbol": "AAPL"})
    assert engine.preview(body, now).active_at == now
    assert size_order(engine, body, now, 100).max_quantity >= 100
    # When: 提交并按完整区间成交。
    order = engine.place(body, now)
    quote = sample(engine, order)
    engine.process(order.symbol, (quote,), quote.market_time + timedelta(seconds=1))
    # Then: T+0 买入正常成交并立即可卖。
    state = engine.store.read()
    assert state.orders[0].status == "filled"
    assert state.lots[0].sellable_at == quote.market_time


def test_retry_when_current_calendar_is_unknown(engine: Engine) -> None:
    # Given: 已有成功请求，再进入没有日历的日期。
    configure(engine, NORMAL)
    body = request()
    original = engine.place(body, NORMAL)
    before = dump(engine.store.path)
    configure(engine, UNKNOWN)
    # When: 原请求编号、原参数重试。
    repeated = engine.place(body, UNKNOWN)
    # Then: 返回原单，数据库无任何变化。
    assert repeated == original
    assert dump(engine.store.path) == before


def test_legacy_order_terminates_when_fill_needs_unknown_settlement(engine: Engine) -> None:
    # Given: 模拟修复前已受理的最后覆盖日委托，保留真实预留金额。
    configure(engine, NORMAL)
    original = engine.place(request(), NORMAL)
    legacy = original.model_copy(
        update={
            "created_at": LAST,
            "active_at": LAST,
            "expires_at": original.expires_at + timedelta(days=1),
        }
    )
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders[0] = legacy
        engine.store.save(connection, state)
    before = engine.store.read()
    assert available(before, "CNY") < before.cash["CNY"]
    configure(engine, LAST)
    quote = sample(engine, legacy)
    # When: 旧单后台处理到可成交完整样本。
    engine.process(legacy.symbol, (quote,), quote.market_time + timedelta(seconds=1))
    # Then: 保留原成交安全检查，终止并释放，无资金流水或持仓变化。
    after = engine.store.read()
    assert after.orders[0].status == "terminated"
    assert "calendar_unavailable" in after.orders[0].status_reason
    assert available(after, "CNY") == after.cash["CNY"]
    assert after.cash == before.cash
    assert after.ledger == before.ledger
    assert after.lots == before.lots


@pytest.mark.parametrize("now", [PREOPEN, LAST], ids=["preopen-dec31", "intraday-dec31"])
def test_real_http_rejects_when_settlement_calendar_missing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, now: datetime
) -> None:
    # Given: 回环临时端口、独立数据库和随机测试口令，不启动后台。
    configure(engine, now)
    monkeypatch.setattr(Clock, "current", now)
    monkeypatch.setattr(trading_routes, "datetime", Clock)
    app = create_app(engine, PASSWORD, run_worker=False)
    before = dump(engine.store.path)
    rows: list[str] = []
    # When: 认证后经真实 HTTP 调用三个产品入口。
    with server(app) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        for route in ["/api/orders/preview", "/api/orders/size", "/api/orders"]:
            response = http.post(url + route, json=request().model_dump(mode="json"))
            rows.append(f"{now.isoformat()} POST {route} {response.status_code} {response.text}")
            receipt(f"calendar-http-{now.strftime('%m%d-%H%M')}.txt", "\n".join(rows))
            # Then: HTTP 错误码与数据库无写入证据一致。
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "calendar_unavailable"
            assert dump(engine.store.path) == before
