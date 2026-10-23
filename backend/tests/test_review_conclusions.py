"""两个真实 HTTP 客户端验证覆盖与重试，不接触正式库。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

from app.api_models import ResetInput
from app.engine import Engine
from app.main import create_app
from app.operations import reset
from app.plan_reviews import reviews
from app.plans import create_plan, update_plan
from app.review_conclusions import detail, save_conclusion
from app.review_core_models import ConclusionInput
from app.review_repository import read_context
from app.storage import Store
from tests.socket_support import client, server
from tests.test_api import PASSWORD
from tests.test_engine import NOW, request, sample
from tests.test_plans import body, edit


def submission(engine: Engine, plan_id: str) -> ConclusionInput:
    loaded = detail(read_context(engine.store), ("plan", plan_id), NOW)
    return ConclusionInput(
        request_id=uuid4(), round_id=UUID(loaded.round_id), conclusion="记录判断", coverage=loaded.coverage
    )


def test_real_http_concurrent_conclusions_retry_and_input(engine: Engine) -> None:
    plan = create_plan(engine, body(), NOW)
    path = f"/api/reviews/plan/{plan.id}"
    with server(create_app(engine, PASSWORD, run_worker=False)) as url, client() as first, client() as second:
        assert first.get(url + path).status_code == 401
        for http in (first, second):
            assert http.post(url + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        data = submission(engine, plan.id)
        blank = data.model_dump(mode="json")
        blank["conclusion"] = "   "
        assert first.post(url + path + "/conclusions", json=blank).status_code == 422

        def submit(index: int) -> int:
            http = first if index == 0 else second
            payload = data.model_copy(update={"request_id": uuid4()})
            return http.post(url + path + "/conclusions", json=payload.model_dump(mode="json")).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(submit, range(2))) == [200, 409]
        fresh = submission(engine, plan.id)
        saved = first.post(url + path + "/conclusions", json=fresh.model_dump(mode="json"))
        assert saved.status_code == 200
        assert first.post(url + path + "/conclusions", json=fresh.model_dump(mode="json")).content == saved.content
        changed = fresh.model_copy(update={"conclusion": "改变正文"})
        assert first.post(url + path + "/conclusions", json=changed.model_dump(mode="json")).status_code == 409
        assert len(read_context(engine.store).conclusions) == 2


def test_successful_retry_after_fill_supplement_and_reset_retains_original(engine: Engine) -> None:
    plan = create_plan(engine, body(), NOW)
    original = submission(engine, plan.id)
    saved = save_conclusion(engine.store, ("plan", plan.id), original)
    order = engine.place(request().model_copy(update={"plan_id": plan.id, "plan_version": 1}), NOW)
    engine.process(order.symbol, (sample(engine, order),), NOW + timedelta(minutes=2))
    retry = save_conclusion(engine.store, ("plan", plan.id), original)
    assert retry.conclusion == saved.conclusion
    assert retry.review.review_state == "needs_update"
    second = save_conclusion(engine.store, ("plan", plan.id), submission(engine, plan.id))
    retry = save_conclusion(engine.store, ("plan", plan.id), original)
    assert retry.review.review_state == "reviewed"
    assert retry.review.coverage.latest_conclusion_id == second.conclusion.id
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW + timedelta(days=1))
    retry = save_conclusion(Store(engine.store.path), ("plan", plan.id), original)
    assert retry.read_only
    assert retry.round_id == plan.round_id != engine.store.read().id
    assert retry.conclusion == saved.conclusion
    assert read_context(engine.store).conclusions == []


def test_date_version_and_archive_states(engine: Engine) -> None:
    plan = create_plan(engine, body().model_copy(update={"review_date": NOW.date() + timedelta(days=1)}), NOW)
    before = detail(read_context(engine.store), ("plan", plan.id), NOW)
    assert before.review.review_state == "not_due"
    original = submission(engine, plan.id)
    # 使用无日期计划验证主动复盘，然后真实改版使覆盖失效。
    plain = update_plan(engine, plan.id, edit(plan).model_copy(update={"review_date": None}))
    saved = save_conclusion(engine.store, ("plan", plan.id), submission(engine, plan.id))
    assert saved.review.review_state == "reviewed"
    _ = update_plan(engine, plan.id, edit(plain).model_copy(update={"review_date": plan.review_date}))
    assert "计划版本已变化" in reviews(engine.store, engine.store.read(), NOW)[0].update_reasons
    assert original.coverage.due_review_date is None
    _ = reset(engine, ResetInput(request_id=str(uuid4()), confirmation="重新开始"), NOW)
    historical = reviews(engine.store, engine.store.read_round(plan.round_id), NOW + timedelta(days=500))[0]
    assert historical.coverage.due_review_date is None
