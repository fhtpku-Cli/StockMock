"""旧换汇表单跨轮次的真实 HTTP 回归。"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.api_models import ConvertInput
from app.engine import Engine
from app.main import create_app
from app.storage import read_rows
from tests.round_support import earn_dollars, reset_http, set_clock
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider


def test_stale_conversion_form_rejects_new_round_with_available_dollars(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 两轮分别通过买卖获得外币，表单保留第一轮编号。
    now = NOW.replace(hour=15)
    set_clock(monkeypatch, now)
    engine.provider = ControlledProvider(quote_time=now - timedelta(minutes=1))
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        earn_dollars(engine, http, url, now)
        old_id = engine.store.read().id
        payload = {"request_id": str(uuid4()), "currency": "USD", "amount": "100", "expected_round_id": old_id}
        reset_http(http, url)
        engine.provider = ControlledProvider(quote_time=now + timedelta(minutes=15))
        earn_dollars(engine, http, url, now + timedelta(minutes=15))
        before = engine.store.read()
        archived = engine.store.read_round(old_id)
        # When: 旧表单首次提交到已有足额美元的新轮。
        response = http.post(url + "/api/fx/convert", json=payload)
        # Then: 拒绝跨轮写入，两轮账本均保持。
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "round_changed"
        assert engine.store.read() == before
        assert engine.store.read_round(old_id) == archived


@pytest.mark.parametrize("legacy", [False, True])
def test_completed_conversion_replays_after_reset_with_original_payload(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, *, legacy: bool
) -> None:
    # Given: 正常交易获得美元，完成一笔带轮次或旧格式的换汇。
    now = NOW.replace(hour=15)
    set_clock(monkeypatch, now)
    engine.provider = ControlledProvider(quote_time=now - timedelta(minutes=1))
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as http:
        assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        earn_dollars(engine, http, url, now)
        old_id = engine.store.read().id
        operation = ConvertInput(
            request_id=str(uuid4()),
            currency="USD",
            amount=Decimal(100),
            expected_round_id=None if legacy else old_id,
        )
        payload = operation.model_dump(mode="json", exclude_none=True)
        assert http.post(url + "/api/fx/convert", json=payload).status_code == 200
        assert engine.store.read().cash["USD"] == Decimal("0.95")
        if legacy:
            # 旧版本数据库记录没有新增字段，直接验证其精确序列化格式。
            with engine.store.transaction() as connection:
                stored = read_rows(connection, "SELECT payload FROM requests WHERE id=?", (operation.request_id,))
            assert stored[0][0] == (
                'convert:{"request_id":"' + operation.request_id + '","currency":"USD","amount":"100"}'
            )
        reset_http(http, url)
        archived = engine.store.read_round(old_id)
        current = engine.store.read()
        # When: 跨轮重试已经成功的同编号请求。
        replay = http.post(url + "/api/fx/convert", json=payload)
        # Then: 返回成功且两轮资金与流水没有再次变化。
        assert replay.status_code == 200, replay.text
        assert engine.store.read_round(old_id) == archived
        assert engine.store.read() == current
