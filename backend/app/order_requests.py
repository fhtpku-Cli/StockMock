"""兼容旧请求结构，并从订单所属轮次返回幂等重试的最终记录。"""

import sqlite3
from typing import Final, Literal

from pydantic import TypeAdapter, ValidationError

from app.models import DomainError, Model, Order, OrderInput
from app.storage import read_rows


class OrderRejection(Model):
    """明确恢复重试形成的最终拒绝，与成功委托共享编号唯一约束。"""

    kind: Literal["order_rejection"] = "order_rejection"
    code: str
    message: str

    def error(self) -> DomainError:
        """只有已保存的拒绝才可作为前端解除未知状态的依据。"""
        return DomainError("order_receipt_rejected", f"此编号的委托已拒绝，{self.message}", 409)


ORDER_RESULT: Final[TypeAdapter[Order | OrderRejection]] = TypeAdapter(Order | OrderRejection)


def saved_result(connection: sqlite3.Connection, response: str) -> Order:
    """读取历史成功记录或已提交的最终拒绝。"""
    result = ORDER_RESULT.validate_json(response)
    if isinstance(result, OrderRejection):
        raise result.error()
    return latest_order(connection, result)


def same_request(payload: str, request: OrderInput) -> bool:
    """旧结构按当前默认值解析，其他类型请求仍不能复用编号。"""
    try:
        saved = OrderInput.model_validate_json(payload)
    except ValidationError:
        return False
    return saved == request


def retry_order(connection: sqlite3.Connection, request: OrderInput) -> Order | None:
    """已处理请求先校验内容，再读取最终拒绝或当前、归档订单。"""
    rows = read_rows(connection, "SELECT payload,response FROM requests WHERE id=?", (request.request_id,))
    if not rows:
        return None
    if not same_request(rows[0][0], request):
        raise DomainError("idempotency_conflict", "相同请求编号不能用于不同操作")
    return saved_result(connection, rows[0][1])


def reject_order(connection: sqlite3.Connection, request: OrderInput, rejection: OrderRejection) -> Order | None:
    """在受理使用的同一写事务锁下确定结果；调用方提交后再抛出拒绝。"""
    saved = retry_order(connection, request)
    if saved is not None:
        return saved
    _ = connection.execute(
        "INSERT INTO requests VALUES(?,?,?)",
        (request.request_id, request.model_dump_json(), rejection.model_dump_json()),
    )
    return None


def latest_order(connection: sqlite3.Connection, saved: Order) -> Order:
    """从当前或归档轮次提取单笔最新委托，避免解析其他轮次的完整账本。"""
    rows = read_rows(
        connection,
        """SELECT entry.value FROM rounds, json_each(rounds.data, '$.orders') AS entry
        WHERE json_extract(entry.value, '$.id')=?
        ORDER BY rounds.active DESC, rounds.rowid DESC LIMIT 1""",
        (saved.id,),
    )
    return Order.model_validate_json(rows[0][0]) if rows else saved


def order_receipt(connection: sqlite3.Connection, request_id: str) -> Order | None:
    """按原请求编号读取已受理委托，排除共享幂等表中的其他操作。"""
    rows = read_rows(connection, "SELECT payload,response FROM requests WHERE id=?", (request_id,))
    if not rows:
        return None
    try:
        _ = OrderInput.model_validate_json(rows[0][0])
    except ValidationError:
        return None
    return saved_result(connection, rows[0][1])


def check_expected_round(request: OrderInput, round_id: str) -> None:
    """新草稿不可跨轮受理；已成功的幂等请求必须在调用前优先返回。"""
    if request.expected_round_id is not None and request.expected_round_id != round_id:
        raise DomainError("round_changed", "草稿所属练习轮次已变化，请刷新后重新建立委托")
