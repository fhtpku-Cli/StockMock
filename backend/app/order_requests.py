"""兼容旧请求结构，并从订单所属轮次返回幂等重试的最终记录。"""

import sqlite3

from pydantic import ValidationError

from app.models import DomainError, Order, OrderInput, State
from app.storage import read_rows


def same_request(payload: str, request: OrderInput) -> bool:
    """旧结构按当前默认值解析，其他类型请求仍不能复用编号。"""
    try:
        saved = OrderInput.model_validate_json(payload)
    except ValidationError:
        return False
    return saved == request


def retry_order(connection: sqlite3.Connection, request: OrderInput) -> Order | None:
    """已成功请求先校验内容，再读取当前或归档订单；不改写历史 JSON。"""
    rows = read_rows(connection, "SELECT payload,response FROM requests WHERE id=?", (request.request_id,))
    if not rows:
        return None
    if not same_request(rows[0][0], request):
        raise DomainError("idempotency_conflict", "相同请求编号不能用于不同操作")
    saved = Order.model_validate_json(rows[0][1])
    for row in read_rows(connection, "SELECT data FROM rounds ORDER BY active DESC,rowid DESC"):
        state = State.model_validate_json(row[0])
        order = next((order for order in state.orders if order.id == saved.id), None)
        if order is not None:
            return order
    return saved
