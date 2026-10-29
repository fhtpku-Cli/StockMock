"""R03：预览截止保护、事务回滚及请求幂等的受控时钟场景。"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.accounting import available
from app.engine import Engine
from app.models import DomainError
from app.sizing import size_order
from app.storage import read_rows
from tests.test_engine import request
from tests.test_opening_limit import OpeningProvider


def prepare_us(engine: Engine) -> datetime:
    now = datetime.fromisoformat("2026-10-22T19:59:50+00:00")
    engine.provider = OpeningProvider(quote_time=now - timedelta(minutes=5))
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(100000)
        engine.store.save(connection, state)
    return now


def test_close_crossing_rejects_without_reservation_or_request(engine: Engine) -> None:
    # Given: 美股收盘前预览。
    before_close = prepare_us(engine)
    body = request().model_copy(update={"symbol": "AAPL"})
    preview = engine.preview(body, before_close)
    expected = body.model_copy(update={"expected_expires_at": preview.expires_at})
    before = engine.store.read()
    # When: 收盘五秒后提交。
    with pytest.raises(DomainError) as error:
        _ = engine.place(expected, preview.expires_at + timedelta(seconds=5))
    # Then: 整个写事务未产生记录或占用。
    assert (error.value.code, error.value.status) == ("validity_changed", 409)
    assert engine.store.read() == before
    assert available(engine.store.read(), "USD") == Decimal(100000)
    with engine.store.transaction() as connection:
        assert not read_rows(connection, "SELECT id FROM requests")


@pytest.mark.parametrize(
    ("preview_time", "submit_time", "quote_time"),
    [
        ("03:29", "03:31", "2026-10-22T03:25:00+00:00"),
        ("01:29", "01:31", "2026-10-21T06:55:00+00:00"),
    ],
)
def test_same_day_validity_stays_accepted(engine: Engine, preview_time: str, submit_time: str, quote_time: str) -> None:
    # Given: 午休前或开盘前预览，截止仍在同一交易日。
    before = datetime.fromisoformat(f"2026-10-22T{preview_time}:00+00:00")
    after = datetime.fromisoformat(f"2026-10-22T{submit_time}:00+00:00")
    engine.provider = OpeningProvider(quote_time=datetime.fromisoformat(quote_time))
    body = request()
    preview = engine.preview(body, before)
    # When: 跨午休/开盘提交。
    order = engine.place(body.model_copy(update={"expected_expires_at": preview.expires_at}), after)
    # Then: 只比较截止，active_at变化不误拒。
    assert order.status == "accepted"
    assert order.expires_at == preview.expires_at


def test_legacy_request_without_expected_expiration_keeps_behavior(engine: Engine) -> None:
    # Given: 旧客户端不传截止字段。
    before_close = prepare_us(engine)
    body = request().model_copy(update={"symbol": "AAPL"})
    old_expiration = engine.preview(body, before_close).expires_at
    # When: 收盘后提交旧请求。
    order = engine.place(body, old_expiration + timedelta(seconds=5))
    # Then: 保持既有次日受理行为。
    assert order.status == "accepted"
    assert order.expires_at > old_expiration


def test_successful_request_retry_precedes_validity_change(engine: Engine) -> None:
    # Given: 收盘前已成功的带预期截止请求。
    before_close = prepare_us(engine)
    body = request().model_copy(update={"symbol": "AAPL"})
    preview = engine.preview(body, before_close)
    expected = body.model_copy(update={"expected_expires_at": preview.expires_at})
    original = engine.place(expected, before_close)
    # When: 收盘后用相同请求编号和参数重试。
    retry = engine.place(expected, preview.expires_at + timedelta(seconds=5))
    # Then: 原单优先返回，无第二单。
    assert retry == original
    assert engine.store.read().orders == [original]


def test_preview_and_sizing_ignore_expected_expiration(engine: Engine) -> None:
    # Given: 过期的预览截止字段。
    before_close = prepare_us(engine)
    body = request().model_copy(update={"symbol": "AAPL"})
    old = engine.preview(body, before_close)
    expected = body.model_copy(update={"expected_expires_at": old.expires_at})
    after_close = old.expires_at + timedelta(seconds=5)
    # When: 只读重新预览与数量计算。
    new = engine.preview(expected, after_close)
    sizing = size_order(engine, expected, after_close, 25)
    # Then: 得到新的有效期和可用数量，无委托写入。
    assert new.expires_at > old.expires_at
    assert sizing.suggested_quantity > 0
    assert not engine.store.read().orders
