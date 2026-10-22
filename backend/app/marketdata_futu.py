"""仅连接本机 OpenD 的官方 JSON 协议；不登录、不访问真实交易接口。"""

import hashlib
import socket
from datetime import datetime, timedelta
from typing import ClassVar, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.marketdata_models import MarketDataError

MAX_TCP_PORT: Final = 65535
_MAX_BODY_BYTES: Final = 1_000_000
_INIT_PROTOCOL: Final = 1001


class _Status(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    ret_type: int = Field(alias="retType")
    err_code: int = Field(default=0, alias="errCode")


class FutuOpenD:
    """短连接发送初始化及历史行情请求，原始响应交给独立边界解析。"""

    def __init__(self, port: int = 11111) -> None:
        """仅允许本机端口；OpenD 本人的登录和行情权限由其自身管理。"""
        if not 0 < port <= MAX_TCP_PORT:
            raise MarketDataError("futu_invalid_port", "端口必须在 1 到 65535 之间")
        self.port: int = port

    @staticmethod
    def _receive(connection: socket.socket, count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            chunk = connection.recv(count - len(result))
            if not chunk:
                raise MarketDataError("futu_connection_closed", "OpenD 关闭连接")
            result.extend(chunk)
        return bytes(result)

    @staticmethod
    def _exchange(connection: socket.socket, packet: bytes) -> bytes:
        connection.sendall(packet)
        header = FutuOpenD._receive(connection, 44)
        size = int.from_bytes(header[12:16], "little")
        if header[:12] != packet[:12] or not 0 < size <= _MAX_BODY_BYTES:
            raise MarketDataError("futu_invalid_packet", "协议头或响应大小不符")
        body = FutuOpenD._receive(connection, size)
        if hashlib.sha1(body, usedforsecurity=False).digest() != header[16:36]:
            raise MarketDataError("futu_invalid_packet", "OpenD 协议完整性校验失败")
        try:
            status = _Status.model_validate_json(body)
        except ValidationError as exc:
            raise MarketDataError("futu_invalid_response", "无法解析 OpenD 返回状态") from exc
        if status.ret_type != 0:
            raise MarketDataError(
                "futu_permission_or_request_error", f"code={status.err_code}; 请检查本人登录和行情权限"
            )
        return body

    def history_bytes(self, code: str) -> bytes:
        """请求最近七天港股五分钟不复权数据；不订阅、不涉及下单。"""
        day = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
        init = _Request(c2s={"clientVer": 100, "clientID": "stockmock-personal", "recvNotify": False})
        history = _Request(
            c2s={
                "rehabType": 0,
                "klType": 6,
                "security": {"market": 1, "code": code},
                "beginTime": (day - timedelta(days=7)).isoformat(),
                "endTime": day.isoformat(),
                "maxAckKLNum": 1000,
                "extendedTime": False,
            }
        )
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as connection:
                connection.settimeout(15)
                _ = self._exchange(connection, encode_packet(init, 1001))
                return self._exchange(connection, encode_packet(history, 3103))
        except OSError as exc:
            raise MarketDataError("futu_unavailable", "本机 OpenD 未运行或连接失败") from exc


class _Request(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    c2s: dict[str, JsonValue] = Field(default_factory=dict)


def encode_packet(request: _Request, protocol: int) -> bytes:
    """按 OpenD 固定小端协议头编码；SHA1 是官方传输格式必需字段。"""
    body = request.model_dump_json().encode()
    serial = 1 if protocol == _INIT_PROTOCOL else 2
    return (
        b"FT"
        + protocol.to_bytes(4, "little")
        + b"\x01\x00"
        + serial.to_bytes(4, "little")
        + len(body).to_bytes(4, "little")
        + hashlib.sha1(body, usedforsecurity=False).digest()
        + b"\x00" * 8
        + body
    )
