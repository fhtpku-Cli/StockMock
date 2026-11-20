"""空仓资产提示通过真实 HTTP 验证，保留缺失汇率与持仓状态。"""

from decimal import Decimal

import pytest

from app.engine import Engine
from app.main import create_app
from app.models import Lot, Portfolio
from tests.round_support import set_clock
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        pytest.param((0, False, False), (1000000, "当前无持仓"), id="empty-cny"),
        pytest.param((12, False, True), (1000084, "当前无持仓"), id="empty-usd-complete"),
        pytest.param((12, False, False), (None, "行情缺失或过期"), id="empty-usd-missing-fx"),
        pytest.param((0, True, True), (1010000, "参考估值，持仓收益待核对"), id="holding-complete"),
        pytest.param((12, True, False), (None, "行情缺失或过期"), id="holding-missing-fx"),
    ],
)
def test_portfolio_status_matches_holdings_and_available_valuation(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    setup: tuple[int, bool, bool],
    expected: tuple[int | None, str],
) -> None:
    dollars, holding, fx_available = setup
    expected_total, expected_status = expected
    set_clock(monkeypatch)
    engine.provider = ControlledProvider(fx_available=fx_available)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.cash["USD"] = Decimal(dollars)
        if holding:
            state.lots.append(Lot(symbol="600519.SS", quantity=100, cost=Decimal(9000), sellable_at=NOW))
        engine.store.save(connection, state)
    before = engine.store.read()
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        response = http.get(url + "/api/portfolio")
    assert response.status_code == 200
    result = Portfolio.model_validate_json(response.content)
    assert result.valuation_status == expected_status
    assert result.total_assets_cny == expected_total
    assert bool(result.positions) == holding
    if expected_total is None:
        assert "缺少账户所需有效汇率" in result.warnings
    assert engine.store.read() == before
