"""0.3.0 临时 SQLite 与真实 HTTP 行为证据。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import override
from uuid import uuid4

import pytest

from app.adjustments import apply_action, receive_dividend
from app.analysis_models import LedgerQuery
from app.cash_queries import statement
from app.engine import Engine
from app.marketdata import FxRate, Quote
from app.models import DomainError, Lot, WorkerView
from app.personal_models import PaymentInput
from app.views import portfolio
from tests.test_analysis import evidence
from tests.test_engine import NOW, ControlledProvider, request, sample
from tests.test_personal import action


def test_q01_composition_cash_and_foreign_missing_fx(engine: Engine) -> None:
    # Given: 现金账户后持有美元标的，有效报价但FX故障。
    initial = portfolio(engine, NOW, WorkerView())
    assert initial.composition is not None
    assert initial.composition.cash_percent == 100
    assert initial.composition.positions_cny == 0
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(12)
        state.lots.append(Lot(symbol="AAPL", quantity=2, cost=Decimal(100), sellable_at=NOW))
        engine.store.save(connection, state)
    # 美股最近休市收盘时间，避免新鲜度条件遮蔽FX测试。
    us_close = datetime(2026, 10, 21, 20, tzinfo=UTC)
    engine.provider = ControlledProvider(quote_time=us_close, price=Decimal(100), fx_available=False)
    # When: 同批数据估值。
    result = portfolio(engine, NOW, WorkerView())
    # Then: 原币仍可读，人民币及全部比例未知。
    assert result.total_assets_cny is None
    assert result.positions[0].market_value == 200
    assert result.positions[0].pnl == 100
    assert result.positions[0].market_value_cny is None
    assert result.positions[0].weight_percent is None
    assert result.composition is not None
    assert result.composition.cash_percent is None
    engine.provider = ControlledProvider(quote_time=us_close, price=Decimal(100))
    recovered = portfolio(engine, NOW, WorkerView())
    assert recovered.total_assets_cny == 1001484
    assert recovered.composition is not None
    assert recovered.composition.positions_cny == 1400
    evidence("q02-missing-fx.json", result.model_dump_json())
    evidence("q01-foreign-recovered.json", recovered.model_dump_json())


def test_q02_change_during_quote_retries_consistent_state(engine: Engine) -> None:
    # Given: 首次报价期间提交另一笔现金事件。
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="600519.SS", quantity=100, cost=Decimal(9000), sellable_at=NOW))
        engine.store.save(connection, state)

    class ChangingProvider(ControlledProvider):
        @override
        def quote(self, symbol: str) -> Quote:
            with engine.store.transaction() as connection:
                current = engine.store.load(connection)
                if current.cash["CNY"] == 1000000:
                    current.cash["CNY"] += 7
                    engine.store.save(connection, current)
            return super().quote(symbol)

    engine.provider = ChangingProvider()
    # When: 实际value_account调用中状态变化后自动重读。
    result = portfolio(engine, NOW, WorkerView())
    # Then: 现金与总资产属于同一新状态。
    assert result.cash[0].balance == 1000007
    assert result.total_assets_cny == 1010007
    evidence("q02-concurrent.json", result.model_dump_json())


@pytest.mark.parametrize("offset", [timedelta(days=-6), timedelta(seconds=1)])
def test_q02_invalid_fx_time_preserves_native_value(engine: Engine, offset: timedelta) -> None:
    # Given: 美元持仓的报价有效，FX已过期或在未来。

    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="AAPL", quantity=1, cost=Decimal(90), sellable_at=NOW))
        engine.store.save(connection, state)

    class InvalidFxProvider(ControlledProvider):
        @override
        def fx_rates(self) -> tuple[FxRate, ...]:
            return (FxRate("USD", Decimal(7), NOW + offset, NOW, "controlled-test"),)

    engine.provider = InvalidFxProvider(quote_time=datetime(2026, 10, 21, 20, tzinfo=UTC))
    # When: 资产分析。
    result = portfolio(engine, NOW, WorkerView())
    # Then: 原币保留，不能给出人民币权重。
    assert result.positions[0].market_value == 100
    assert result.positions[0].pnl == 10
    assert result.positions[0].weight_percent is None
    assert result.total_assets_cny is None


def test_q05_receivable_payment_composition_and_cash_statement(engine: Engine) -> None:
    # Given: 受控临时账户的真实成交和应收登记。

    buy = engine.place(request(), NOW)
    engine.process(buy.symbol, (sample(engine, buy, "100"),), NOW + timedelta(minutes=2))
    later = NOW + timedelta(days=1)
    engine.provider = ControlledProvider(quote_time=later)
    registered = apply_action(
        engine,
        action(engine).model_copy(update={"payment_status": "receivable", "effective_date": later.date()}),
        later,
    )
    before = portfolio(engine, later, WorkerView())
    assert before.composition is not None
    assert before.composition.receivables_cny == 123
    assert engine.store.read().ledger[-1].amount == 0
    payment = PaymentInput(request_id=str(uuid4()), round_id=registered.round_id)
    # When: 到账及重试同一个请求。
    _ = receive_dividend(engine, registered.id, payment, later)
    _ = receive_dividend(engine, registered.id, payment, later)
    after = portfolio(engine, later, WorkerView())
    # Then: 只加一次现金，应收消除，总资产不重增，核对无差额。
    assert after.composition is not None
    assert after.composition.receivables_cny == 0
    assert before.total_assets_cny == after.total_assets_cny
    assert after.cash[0].balance == before.cash[0].balance + 123
    result = statement(engine.store.read(), LedgerQuery())
    assert all(row.difference == 0 for row in result.currencies)
    evidence("q05-receivable-before.json", before.model_dump_json())
    evidence("q05-paid-after.json", after.model_dump_json())


def test_q02_continuous_mutations_return_retryable_error(engine: Engine) -> None:
    # Given: 每次报价均发生新现金事件，无法取得稳定账户。

    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.lots.append(Lot(symbol="600519.SS", quantity=100, cost=Decimal(9000), sellable_at=NOW))
        engine.store.save(connection, state)

    class AlwaysChangingProvider(ControlledProvider):
        @override
        def quote(self, symbol: str) -> Quote:
            with engine.store.transaction() as connection:
                current = engine.store.load(connection)
                current.cash["CNY"] += 1
                engine.store.save(connection, current)
            return super().quote(symbol)

    engine.provider = AlwaysChangingProvider()
    # When: 估值尝试达到有界重试上限。
    with pytest.raises(DomainError) as failure:
        _ = portfolio(engine, NOW, WorkerView())
    # Then: 明确可重试，不发布混合总额，仅三次采集。
    assert failure.value.code == "valuation_changed"
    assert failure.value.status == 409
    assert engine.store.read().cash["CNY"] == 1000003


def test_q01_components_add_up_with_fractional_fx(engine: Engine) -> None:
    # Given: 多项小额外币，逐项人民币显示存在舍入。
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal("0.01")
        state.cash["HKD"] = Decimal("0.01")
        state.lots.append(Lot(symbol="AAPL", quantity=3, cost=Decimal("0.01"), sellable_at=NOW))
        engine.store.save(connection, state)

    class FractionalProvider(ControlledProvider):
        @override
        def fx_rates(self) -> tuple[FxRate, ...]:
            return (
                FxRate("USD", Decimal("7.555"), NOW, NOW, "controlled-test"),
                FxRate("HKD", Decimal("0.933"), NOW, NOW, "controlled-test"),
            )

    engine.provider = FractionalProvider(price=Decimal("1.01"), quote_time=datetime(2026, 10, 21, 20, tzinfo=UTC))
    # When: 同次资产估值与构成。
    result = portfolio(engine, NOW, WorkerView())
    # Then: 可显示金额之和等于总资产，分类金额与单证券一致。
    components = result.composition
    assert components is not None
    assert components.cash_cny is not None
    assert components.positions_cny is not None
    assert components.receivables_cny is not None
    assert components.cash_cny + components.positions_cny + components.receivables_cny == result.total_assets_cny
    assert components.by_currency[0].value_cny == result.positions[0].market_value_cny
