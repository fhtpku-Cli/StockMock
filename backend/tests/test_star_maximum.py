"""A股单笔最大数量及快捷仓位建议不能绕过交易所限制。"""

from decimal import Decimal
from pathlib import Path
from typing import Final, Literal
from uuid import uuid4

import pytest

from app.catalog import from_rule, instrument
from app.discovery import Discovery
from app.models import DomainError, Lot, OrderInput, State
from app.order_rules import validate_quantity
from app.sizing import size_order
from tests.test_admission import inject, isolated, security_rule
from tests.test_engine import NOW

_CN_LIMITS: Final[tuple[tuple[str, Literal["market", "limit"], int], ...]] = (
    ("688981.SS", "market", 50_000),
    ("688981.SS", "limit", 100_000),
    ("300750.SZ", "market", 150_000),
    ("300750.SZ", "limit", 300_000),
    ("600000.SS", "market", 1_000_000),
    ("600000.SS", "limit", 1_000_000),
    ("512880.SS", "market", 1_000_000),
    ("512880.SS", "limit", 1_000_000),
)


@pytest.mark.parametrize("case", _CN_LIMITS)
@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("increment", [0, 1])
def test_cn_maximum_applies_to_buys_and_full_liquidation(
    case: tuple[str, Literal["limit", "market"], int],
    side: Literal["buy", "sell"],
    increment: int,
) -> None:
    # Given: 数量恰好上限或超出1股，卖出时特意令数量等于全部持仓。
    symbol, order_type, maximum = case
    item = from_rule(security_rule(symbol))
    quantity = maximum + increment
    state = State(
        id=str(uuid4()),
        created_at=NOW,
        lots=[Lot(symbol=item.symbol, quantity=quantity, cost=Decimal(quantity), sellable_at=NOW)],
    )
    request = OrderInput(
        request_id=str(uuid4()),
        symbol=item.symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        limit_price=Decimal(1),
    )
    # When / Then: 上限允许；超限即使全仓卖清也必须拒绝。
    if increment:
        with pytest.raises(DomainError) as error:
            validate_quantity(item, state, request)
        assert error.value.code == "quantity_limit"
    else:
        validate_quantity(item, state, request)


@pytest.mark.parametrize("case", _CN_LIMITS)
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_cn_sizing_caps_maximum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, Literal["limit", "market"], int],
    side: Literal["buy", "sell"],
) -> None:
    # Given: 充足资金及超过单笔上限的真实SQLite持仓。
    symbol, order_type, maximum = case
    rule = security_rule(symbol)
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    _ = engine.registry.enable(rule.symbol, Discovery())
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["CNY"] = Decimal(1_000_000_000)
        state.lots.append(
            Lot(symbol=rule.symbol, quantity=maximum + 50_000, cost=Decimal((maximum + 50_000) * 100), sellable_at=NOW)
        )
        engine.store.save(connection, state)
    request = OrderInput(
        request_id=str(uuid4()),
        symbol=rule.symbol,
        side=side,
        order_type=order_type,
        quantity=200,
        limit_price=Decimal(100),
    )
    # When: 请求全仓快捷数量。
    result = size_order(engine, request, NOW, 100)
    # Then: 建议不超过单笔上限，并且能被同一订单构建路径受理。
    assert result.max_quantity == maximum
    assert result.suggested_quantity == maximum
    assert engine.preview(request.model_copy(update={"quantity": result.suggested_quantity}), NOW)


@pytest.mark.parametrize(
    ("holding", "percent", "suggested"), [(301, 25, 0), (301, 100, 301), (100, 25, 0), (100, 100, 100)]
)
def test_star_sell_suggestion_keeps_minimum_except_whole_remainder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    holding: int,
    percent: int,
    suggested: int,
) -> None:
    # Given: 可卖科创持仓，比例建议可能少于200股。
    rule = security_rule("688981.SS")
    inject(monkeypatch, rule)
    engine = isolated(tmp_path)
    _ = engine.registry.enable(rule.symbol, Discovery())
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol=rule.symbol, quantity=holding, cost=Decimal(holding * 100), sellable_at=NOW))
        engine.store.save(connection, state)
    request = OrderInput(
        request_id=str(uuid4()),
        symbol=rule.symbol,
        side="sell",
        order_type="limit",
        quantity=200,
        limit_price=Decimal(100),
    )
    # When / Then: 不合法的小额部分卖出建议为0，完整零股可清仓。
    result = size_order(engine, request, NOW, percent)
    assert result.suggested_quantity == suggested
    if suggested:
        assert engine.preview(request.model_copy(update={"quantity": suggested}), NOW)


@pytest.mark.parametrize("symbol", ["AAPL", "0700.HK"])
@pytest.mark.parametrize("order_type", ["market", "limit"])
def test_cn_limits_do_not_reduce_us_or_hk_input_range(
    symbol: str,
    order_type: Literal["market", "limit"],
) -> None:
    # Given: 美港股仍遵循既有模拟输入上限，港股这里仅验证数量，未启用交易。
    item = instrument(symbol)
    state = State(id=str(uuid4()), created_at=NOW)
    request = OrderInput(
        request_id=str(uuid4()),
        symbol=symbol,
        side="buy",
        order_type=order_type,
        quantity=10_000_000,
        limit_price=Decimal(1),
    )
    # When / Then: 新A股板块上限不误应用到美港股。
    validate_quantity(item, state, request)
