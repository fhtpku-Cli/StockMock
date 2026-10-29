"""正式结论的同连接覆盖校验与幂等追加事务。"""

from datetime import UTC, datetime

from app.models import DomainError
from app.order_views import order_view
from app.plan_reviews import context_reviews
from app.review_core_models import Conclusion, ConclusionInput, SubjectType
from app.review_models import ConclusionResult, ReviewDetail
from app.review_repository import ReviewContext, load_context
from app.storage import Store, read_rows


def detail(context: ReviewContext, subject: tuple[SubjectType, str], now: datetime) -> ReviewDetail:
    """主体归属必须精确；有计划的委托没有独立完成状态。"""
    kind, identifier = subject
    if kind == "order":
        order = next((o for o in context.state.orders if o.id == identifier), None)
        if order is not None and order.plan_id is not None:
            raise DomainError("review_plan_required", "有关联计划的委托请在对应计划内复盘")
    review = next((r for r in context_reviews(context, now) if r.subject_type == kind and r.id == identifier), None)
    if review is None:
        raise DomainError("review_missing", "本轮次中不存在该复盘主体", 404)
    orders = [o for o in context.state.orders if o.plan_id == identifier] if review.plan else [review.legacy_order]
    return ReviewDetail(
        round_id=context.state.id,
        read_only=context.state.archived_at is not None,
        review=review,
        coverage=review.coverage,
        orders=[order_view(o) for o in orders if o is not None],
        conclusions=[c for c in context.conclusions if (c.subject_type, c.subject_id) == subject],
    )


def save_conclusion(store: Store, subject: tuple[SubjectType, str], body: ConclusionInput) -> ConclusionResult:
    """先处理已成功请求，再检查首次提交的轮次与事实。"""
    with store.transaction() as connection:
        now = datetime.now(UTC)
        rows = read_rows(connection, "SELECT payload,data FROM review_conclusions WHERE id=?", (str(body.request_id),))
        if rows:
            conclusion = Conclusion.model_validate_json(rows[0][1])
            if rows[0][0] != body.model_dump_json() or (conclusion.subject_type, conclusion.subject_id) != subject:
                raise DomainError("request_conflict", "相同请求编号不能提交不同内容")
            current = detail(load_context(connection, store, conclusion.round_id), subject, now)
            return ConclusionResult(
                conclusion=conclusion, round_id=current.round_id, read_only=current.read_only, review=current.review
            )
        context = load_context(connection, store, str(body.round_id))
        current = detail(context, subject, now)
        if context.state.archived_at is not None or store.load(connection).id != str(body.round_id):
            raise DomainError("archived_review", "轮次已切换，归档复盘只读；请保留草稿")
        if body.coverage != current.coverage:
            raise DomainError("review_conflict", "事实已变化，请刷新后保留草稿重新保存")
        conclusion = Conclusion(
            id=str(body.request_id),
            round_id=current.round_id,
            subject_type=subject[0],
            subject_id=subject[1],
            conclusion=body.conclusion,
            deviation=body.deviation,
            next_action=body.next_action,
            created_at=now,
            coverage=body.coverage,
        )
        _ = connection.execute(
            "INSERT INTO review_conclusions VALUES(?,?,?,?,?,?)",
            (
                conclusion.id,
                conclusion.round_id,
                conclusion.subject_type,
                conclusion.subject_id,
                body.model_dump_json(),
                conclusion.model_dump_json(),
            ),
        )
        context.conclusions.append(conclusion)
        saved = detail(context, subject, now)
        return ConclusionResult(
            conclusion=conclusion, round_id=saved.round_id, read_only=saved.read_only, review=saved.review
        )
