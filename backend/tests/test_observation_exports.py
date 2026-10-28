"""HTTP 响应、CSV 下载、旧 JSON 和归档的观察依据兼容。"""

import csv
import io
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.api_models import ResetInput
from app.engine import Engine
from app.main import create_app
from app.models import Order
from app.operations import reset
from app.review_models import Fact, FactPage
from app.storage import Store, read_rows
from tests.test_analysis import evidence
from tests.test_api import PASSWORD
from tests.test_engine import NOW, request, sample


def test_http_csv_only_filled_fact_retains_observation(engine: Engine) -> None:
    order = engine.place(request(), NOW)
    _ = engine.cancel(order.id, NOW + timedelta(minutes=5))
    engine.process(order.symbol, (sample(engine, order, "99.1234", 5),), NOW + timedelta(minutes=6))
    before = engine.store.read()
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        response = client.get("/api/orders")
        assert response.status_code == 200
        orders = TypeAdapter(list[Order]).validate_json(response.content)
        assert orders[0].fill_observation is not None
        assert orders[0].fill_observation.price == Decimal("99.1234")
        exported = client.get("/api/reviews/export.csv")
        assert exported.status_code == 200
        rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
        facts = [Fact.model_validate_json(row["事实JSON"]) for row in rows if row["事实JSON"]]
        assert {fact.type for fact in facts} == {"order_submitted", "cancel_requested", "order_filled"}
        for fact in facts:
            assert fact.order is not None
            if fact.type == "order_filled":
                assert fact.order.fill_observation == orders[0].fill_observation
            else:
                assert fact.order.fill_observation is None
        timeline = client.get(f"/api/reviews/order/{order.id}/facts")
        assert timeline.status_code == 200
        page = FactPage.model_validate_json(timeline.content)
        assert page.items == facts
        evidence("facts-api.json", timeline.text)
        evidence("orders-api.json", response.text)
        evidence("review-download.csv", exported.text)
    assert Store(engine.store.path).read() == before
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW + timedelta(minutes=7))
    archived = Store(engine.store.path).read_round(before.id)
    assert archived.orders == before.orders
    assert archived.cash == before.cash
    assert archived.ledger == before.ledger
    engine.process(order.symbol, (sample(engine, order, "98", 5),), NOW + timedelta(minutes=8))
    assert Store(engine.store.path).read_round(before.id) == archived
    assert not engine.store.read().orders
    evidence("new-archived.json", archived.model_dump_json())


def test_legacy_active_watermark_terminal_archive_preserved(engine: Engine) -> None:
    active = engine.place(request(), NOW)
    terminal = engine.place(request(), NOW)
    with engine.store.transaction() as connection:
        state = engine.store.load(connection)
        state.orders[0] = active.model_copy(update={"last_sample_at": NOW + timedelta(minutes=5)})
        state.orders[1] = terminal.model_copy(update={"status": "filled", "fill_price": Decimal(99), "filled_at": NOW})
        data = state.model_dump_json(exclude={"orders": {"__all__": {"fill_observation"}}})
        _ = connection.execute("UPDATE rounds SET data=? WHERE id=?", (data, state.id))
    restarted = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    old = restarted.store.read()
    assert all(order.fill_observation is None for order in old.orders)
    restarted.process(active.symbol, (sample(engine, active, "98", 5),), NOW + timedelta(minutes=6))
    assert restarted.store.read() == old
    assert restarted.store.read().orders[1].fill_price == 99
    _ = restarted.cancel(active.id, NOW)
    expected = restarted.store.read()
    _ = reset(restarted, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    archive = Store(engine.store.path).read_round(old.id)
    assert archive.orders == expected.orders
    assert archive.cash == old.cash
    assert archive.ledger == old.ledger
    assert not restarted.store.read().orders
    with restarted.store.transaction() as connection:
        archived_json = read_rows(connection, "SELECT data FROM rounds WHERE id=?", (old.id,))[0][0]
    evidence("legacy-archived.json", archived_json)
