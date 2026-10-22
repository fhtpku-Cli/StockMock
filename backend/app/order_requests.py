"""旧版下单请求在新增可选计划字段后的幂等比较。"""

from pydantic import ValidationError

from app.models import OrderInput


def same_request(payload: str, request: OrderInput) -> bool:
    """旧结构按当前默认值解析，其他类型请求仍不能复用编号。"""
    try:
        saved = OrderInput.model_validate_json(payload)
    except ValidationError:
        return False
    return saved == request
