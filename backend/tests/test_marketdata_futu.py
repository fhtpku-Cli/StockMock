"""本地受控 TCP 对端与解析测试；不声称富途账户实测通过。"""

import hashlib
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import JsonValue, TypeAdapter

from app.marketdata_futu import FutuOpenD
from app.marketdata_futu_parser import parse_futu_intraday
from app.marketdata_models import MarketDataError


def payload(stamp: str, volume: int = 100) -> bytes:
    row = {
        "time": stamp,
        "isBlank": False,
        "openPrice": 10,
        "highPrice": 12,
        "lowPrice": 9,
        "closePrice": 11,
        "volume": volume,
    }
    return json.dumps({"retType": 0, "s2c": {"security": {"market": 1, "code": "00700"}, "klList": [row]}}).encode()


@pytest.mark.parametrize(("label", "stamp"), [("start", "2026-10-22 09:30:00"), ("end", "2026-10-22 09:35:00")])
def test_interval_when_explicit_label_verified(monkeypatch: pytest.MonkeyPatch, label: str, stamp: str) -> None:
    # Given: 接入验收已显式确认标签口径。
    monkeypatch.setenv("STOCKMOCK_FUTU_BAR_LABEL", label)
    # When: 解析同一完整区间的两种供应商标签。
    bar = parse_futu_intraday(payload(stamp), "0700.HK", datetime(2026, 10, 22, 2, tzinfo=UTC))[0]
    # Then: 得到相同 UTC 起止；无检索日猜测。
    assert (bar.interval_start, bar.market_time) == (
        datetime(2026, 10, 22, 1, 30, tzinfo=UTC),
        datetime(2026, 10, 22, 1, 35, tzinfo=UTC),
    )


def test_reject_when_label_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 未做本人真实行情标签验收。
    monkeypatch.delenv("STOCKMOCK_FUTU_BAR_LABEL", raising=False)
    # When / Then: 不猜测开始/结束语义。
    with pytest.raises(MarketDataError, match="futu_unverified_response"):
        _ = parse_futu_intraday(payload("2026-10-22 09:35:00"), "0700.HK", datetime(2026, 10, 22, 2, tzinfo=UTC))


@pytest.mark.parametrize(
    ("stamp", "volume"),
    [
        ("2026-10-22 12:05:00", 100),
        ("2026-10-22 13:00:00", 100),
        ("2026-10-22 16:05:00", 100),
        ("2026-10-22 09:35:00", 0),
        ("2026-10-23 09:35:00", 100),
    ],
)
def test_no_candidate_when_lunch_auction_zero_or_future(
    monkeypatch: pytest.MonkeyPatch, stamp: str, volume: int
) -> None:
    # Given: 当天收盘后，源可能混入午休、竞价或未完成数据。
    monkeypatch.setenv("STOCKMOCK_FUTU_BAR_LABEL", "end")
    # When: 处理源区间。
    bars = parse_futu_intraday(payload(stamp, volume), "0700.HK", datetime(2026, 10, 22, 9, tzinfo=UTC))
    # Then: 不产生可撮合事件。
    assert bars == ()


def serve_opend(listener: socket.socket, permission: Literal[0, -1]) -> list[dict[str, JsonValue]]:
    requests: list[dict[str, JsonValue]] = []
    with listener.accept()[0] as connection, connection.makefile("rb") as stream:
        connection.settimeout(3)
        for response in (b'{"retType":0,"s2c":{}}', json.dumps({"retType": permission, "errCode": 7}).encode()):
            header = stream.read(44)
            body = stream.read(int.from_bytes(header[12:16], "little"))
            requests.append(TypeAdapter(dict[str, JsonValue]).validate_json(body))
            packet = header[:12] + len(response).to_bytes(4, "little")
            packet += hashlib.sha1(response, usedforsecurity=False).digest() + b"\x00" * 8 + response
            connection.sendall(packet)
    return requests


@pytest.mark.parametrize("permission", [0, -1])
def test_socket_protocol_when_local_opend_replies(permission: Literal[0, -1]) -> None:
    # Given: 真实本机 TCP 连接上的受控 OpenD 对端，端口隔离。
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as executor:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        address = TypeAdapter(tuple[str, int]).validate_python(listener.getsockname())
        future = executor.submit(serve_opend, listener, permission)
        # When: 生产适配器发送初始化和不复权历史请求。
        if permission == 0:
            response = FutuOpenD(address[1]).history_bytes("00700")
            assert TypeAdapter(dict[str, JsonValue]).validate_json(response)["retType"] == 0
        else:
            with pytest.raises(MarketDataError, match="futu_permission_or_request_error"):
                _ = FutuOpenD(address[1]).history_bytes("00700")
        requests = future.result(timeout=3)
    # Then: 明确不复权和五分钟；权限失败不会制造行情。
    history = requests[1]["c2s"]
    assert isinstance(history, dict)
    assert (history["rehabType"], history["klType"], history["security"]) == (0, 6, {"market": 1, "code": "00700"})
