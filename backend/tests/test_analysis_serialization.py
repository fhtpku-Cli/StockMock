"""真实HTTP百分比兼容普通十进制字符串，保留微小非零值。"""

from decimal import Decimal
from typing import Annotated

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, StringConstraints

import app.asset_routes as assets
from app.engine import Engine
from app.main import create_app
from app.models import Lot
from tests.test_analysis import Clock, evidence
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider

PlainDecimal = Annotated[str, StringConstraints(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$")]


class WirePosition(BaseModel):
    pnl_percent: PlainDecimal | None
    weight_percent: PlainDecimal | None


class WireAllocation(BaseModel):
    weight_percent: PlainDecimal | None


class WireComposition(BaseModel):
    cash_percent: PlainDecimal | None
    positions_percent: PlainDecimal | None
    receivables_percent: PlainDecimal | None
    by_market: list[WireAllocation]
    by_currency: list[WireAllocation]


class WirePortfolio(BaseModel):
    composition: WireComposition
    positions: list[WirePosition]


@pytest.mark.parametrize("case", ["pure_cash", "tiny_nonzero", "zero_gain"])
def test_percentage_json_when_zero_or_tiny(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    # Given: 纯现金、极小非零持仓占比与浮动收益率、零收益率三种真实账户。
    Clock.current = NOW
    monkeypatch.setattr(assets, "datetime", Clock)
    engine.provider = ControlledProvider(price=Decimal("0.01"), quote_time=NOW)
    if case != "pure_cash":
        cost = Decimal("0.0099999999999") if case == "tiny_nonzero" else Decimal("0.01")
        with engine.store.transaction() as connection:
            state = engine.store.load(connection)
            state.lots.append(Lot(symbol="600519.SS", quantity=1, cost=cost, sellable_at=NOW))
            engine.store.save(connection, state)
    before = engine.store.read()
    # When: 通过实际HTTP读取响应字节，使用前端相同普通十进制语法验证。
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        response = client.get("/api/portfolio")
        assert response.status_code == 200
        evidence(f"percentage-{case}.json", response.text)
        wire = WirePortfolio.model_validate_json(response.content)
    # Then: 每项百分比都是普通十进制，微小非零值不舍入成零，读取不改账。
    assert engine.store.read() == before
    assert wire.composition.receivables_percent is not None
    assert Decimal(wire.composition.receivables_percent) == 0
    assert wire.composition.positions_percent is not None
    if case == "pure_cash":
        assert Decimal(wire.composition.positions_percent) == 0
        assert wire.composition.cash_percent == "100"
    else:
        expected_weight = Decimal("0.01") / Decimal("1000000.01") * 100
        assert Decimal(wire.composition.positions_percent) == expected_weight
        assert wire.positions[0].weight_percent is not None
        assert Decimal(wire.positions[0].weight_percent) == expected_weight
        assert wire.positions[0].pnl_percent is not None
        expected_pnl = Decimal(0)
        if case == "tiny_nonzero":
            expected_pnl = Decimal("0.0000000000001") / Decimal("0.0099999999999") * 100
        assert Decimal(wire.positions[0].pnl_percent) == expected_pnl
