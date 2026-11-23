"""候选接口示例使用受控报价，所有金额和比例都保留十进制文本。"""

import json
from decimal import Decimal

import pytest

from app.engine import Engine
from app.models import OrderInput, PositionImpact, StopLoss, plan_sync_conflict
from tests.test_engine import NOW, request


def test_preview_response_examples() -> None:
    assert StopLoss(amount=Decimal("102.00")).model_dump(mode="json") == {
        "amount": "102.00",
        "cny": None,
        "asset_percent": None,
    }
    example = PositionImpact(
        status="complete",
        active_orders=0,
        quote_time=NOW,
        current_percent=Decimal("0.00"),
        after_percent=Decimal("1.00"),
    )
    assert example.model_dump(mode="json")["current_percent"] == "0.00"
    assert plan_sync_conflict().code == "plan_sync_conflict"


def test_original_request_examples() -> None:
    legacy = json.loads(request().model_dump_json())
    assert "save_as_plan" not in legacy
    assert "plan_review_date" not in legacy
    sync = OrderInput.model_validate({**legacy, "save_as_plan": True, "reason": "受控测试"})
    assert json.loads(sync.model_dump_json()) == {**legacy, "save_as_plan": True, "reason": "受控测试"}


@pytest.mark.parametrize(("kind", "expected"), [("limit", "1009.50"), ("market", "1209.60")])
def test_stop_loss_includes_both_fees_and_market_bound(engine: Engine, kind: str, expected: str) -> None:
    body = request().model_copy(update={"order_type": kind, "stop_price": Decimal(90)})
    preview = engine.preview(body, NOW)
    assert preview.stop_loss is not None
    assert preview.stop_loss.amount == Decimal(expected)
    assert preview.stop_loss.cny is None
    assert preview.stop_loss.asset_percent is None


@pytest.mark.parametrize("stop", [None, Decimal(100), Decimal(101)])
def test_stop_loss_above_reference_or_missing(engine: Engine, stop: Decimal | None) -> None:
    preview = engine.preview(request().model_copy(update={"stop_price": stop}), NOW)
    assert preview.stop_loss is None
    assert any("不低于" in warning for warning in preview.warnings) == (stop is not None)
