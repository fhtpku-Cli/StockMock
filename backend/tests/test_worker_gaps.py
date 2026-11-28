"""后台成功轮、交易时段中断与近七天健康契约测试。"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app import worker as worker_module
from app.engine import Engine
from app.main import create_app
from app.marketdata import MarketDataError, Quote
from app.models import DomainError, HealthView
from app.storage import Store, read_rows
from app.worker import Worker, gap_markets, gap_summary, record_success
from tests.round_support import MarketClock
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request


def patch_clock(monkeypatch: pytest.MonkeyPatch, clock: list[datetime]) -> None:
    class ControlledClock:
        @staticmethod
        def now(_tz: tzinfo | None = UTC) -> datetime:
            return clock[0]

        @staticmethod
        def fromisoformat(value: str) -> datetime:
            return datetime.fromisoformat(value)

    monkeypatch.setattr(worker_module, "datetime", ControlledClock)


def heartbeat(store: Store) -> datetime | None:
    with store.read_transaction() as connection:
        rows = read_rows(connection, "SELECT last_success FROM worker_heartbeat WHERE id=1")
    return datetime.fromisoformat(rows[0][0]) if rows else None


def raw_gaps(store: Store) -> list[tuple[str, ...]]:
    with store.read_transaction() as connection:
        return read_rows(
            connection,
            "SELECT id,started_at,ended_at,markets,cause FROM worker_gaps ORDER BY ended_at",
        )


def insert_gap(store: Store, gap_id: str, started: datetime, ended: datetime, markets: list[str]) -> None:
    with store.transaction() as connection:
        _ = connection.execute(
            "INSERT INTO worker_gaps VALUES(?,?,?,?,?)",
            (gap_id, started.isoformat(), ended.isoformat(), json.dumps(markets), "paused_or_down"),
        )


def ignore_capture(_engine: Engine) -> None:
    return


def test_first_success_only_baselines_and_five_minutes_does_not_gap(engine: Engine) -> None:
    # Given: 旧库升级后没有心跳行。
    started = datetime.fromisoformat("2026-10-29T02:00:00+00:00")
    # When: 首次成功及恰好五分钟后成功。
    record_success(engine.store, started)
    assert heartbeat(engine.store) == started
    assert raw_gaps(engine.store) == []
    record_success(engine.store, started + timedelta(minutes=5))
    # Then: 首轮动只建基线，阈值包含五分钟，不记中断。
    assert heartbeat(engine.store) == started + timedelta(minutes=5)
    assert raw_gaps(engine.store) == []


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-10-29T05:30:00+00:00", "2026-10-29T05:36:00+00:00", ["CN", "HK"]),
        ("2026-10-29T13:30:00+00:00", "2026-10-29T13:36:00+00:00", ["US"]),
        ("2026-11-02T14:30:00+00:00", "2026-11-02T14:36:00+00:00", ["US"]),
    ],
)
def test_crossing_sessions_records_markets_and_wall_clock_interval(
    engine: Engine, start: str, end: str, expected: list[str]
) -> None:
    # Given: 已持久化的上次成功轮，覆盖美股夏令时与标准时两种 UTC 开盘。
    started, ended = datetime.fromisoformat(start), datetime.fromisoformat(end)
    record_success(engine.store, started)
    # When: 超过五分钟后成功。
    record_success(engine.store, ended)
    # Then: gap 保留完整墙钟端点、重叠市场和固定原因。
    rows = raw_gaps(engine.store)
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == row[1] == started.isoformat()
    assert row[2] == ended.isoformat()
    assert TypeAdapter(list[str]).validate_json(row[3]) == expected
    assert row[4] == "paused_or_down"


def test_fully_closed_weekend_does_not_record_gap(engine: Engine) -> None:
    # Given: 三个市场均休市的周六区间。
    started = datetime.fromisoformat("2026-10-31T03:00:00+00:00")
    record_success(engine.store, started)
    record_success(engine.store, started + timedelta(minutes=30))
    # Then: 心跳照常推进，但不把休市当后台中断。
    assert heartbeat(engine.store) == started + timedelta(minutes=30)
    assert raw_gaps(engine.store) == []


def test_restart_first_success_backfills_persistent_gap(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 进程停止前已有成功基线。
    started = datetime.fromisoformat("2026-10-29T02:00:00+00:00")
    ended = started + timedelta(minutes=10)
    record_success(engine.store, started)
    restarted = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    clock = [ended]
    patch_clock(monkeypatch, clock)
    monkeypatch.setattr(worker_module, "capture", ignore_capture)
    # When: 新 Store、新 Engine、新 Worker 的首个成功轮。
    Worker(restarted).tick()
    # Then: 不依赖进程内 last_tick，从持久心跳补记停服区间。
    assert heartbeat(restarted.store) == ended
    assert len(raw_gaps(restarted.store)) == 1


def test_account_failure_preserves_heartbeat_and_recovery_uses_old_baseline(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 已有基线，随后账户读取失败一轮。
    clock = [datetime.fromisoformat("2026-10-29T02:00:00+00:00")]
    record_success(engine.store, clock[0])
    worker = Worker(engine)
    patch_clock(monkeypatch, clock)
    monkeypatch.setattr(worker_module, "capture", ignore_capture)

    def fail_read(_store: Store) -> None:
        raise DomainError("controlled_read_failure", "受控账户读取失败")

    with monkeypatch.context() as patch:
        patch.setattr(Store, "read", fail_read)
        clock[0] += timedelta(minutes=10)
        worker.tick()
    assert heartbeat(engine.store) == clock[0] - timedelta(minutes=10)
    assert raw_gaps(engine.store) == []
    # When: 再过两分钟恢复成功。
    clock[0] += timedelta(minutes=2)
    worker.tick()
    # Then: 中断从最后持久成功开始，而不是失败轮或恢复轮重新计时。
    assert heartbeat(engine.store) == clock[0]
    rows = raw_gaps(engine.store)
    assert len(rows) == 1
    assert rows[0][1] == (clock[0] - timedelta(minutes=12)).isoformat()
    assert rows[0][2] == clock[0].isoformat()


@pytest.mark.parametrize("stage", ["process", "capture"])
@pytest.mark.parametrize("use_domain_error", [True, False])
def test_processing_or_capture_failure_does_not_advance_heartbeat(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, stage: Literal["process", "capture"], *, use_domain_error: bool
) -> None:
    # Given: 一个活动订单及最后成功基线，分别注入两类规定的处理失败。
    _ = engine.place(request(), NOW)
    record_success(engine.store, NOW)
    clock = [NOW + timedelta(minutes=1)]
    patch_clock(monkeypatch, clock)
    error = DomainError("controlled_failure", "受控处理失败") if use_domain_error else sqlite3.OperationalError()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    if stage == "process":
        monkeypatch.setattr(Engine, "process", fail)
    else:
        monkeypatch.setattr(worker_module, "capture", fail)
    # When / Then: 任意 engine.process 或 capture 失败都保留原心跳。
    Worker(engine).tick()
    assert heartbeat(engine.store) == NOW
    assert raw_gaps(engine.store) == []


def test_market_data_failure_still_counts_as_live_success(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 行情获取失败，但订单截止处理和资产观察均未发生规定的处理错误。
    _ = engine.place(request(), NOW)
    record_success(engine.store, NOW)
    clock = [NOW + timedelta(minutes=5)]
    patch_clock(monkeypatch, clock)

    def outage(_provider: ControlledProvider, _symbol: str) -> tuple[Quote, ...]:
        raise MarketDataError("controlled_outage", "受控行情中断")

    monkeypatch.setattr(ControlledProvider, "intraday", outage)
    worker = Worker(engine)
    worker.tick()
    # Then: 行情异常状态保留，但后台活性心跳推进，且阈值内没有 gap。
    assert worker.view.status == "行情异常，按截止规则处理"
    assert heartbeat(engine.store) == clock[0]
    assert raw_gaps(engine.store) == []


def test_heartbeat_write_failure_does_not_change_processing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Given: 可正常成交的订单，心跳写入独立故障。
    placed = engine.place(request(), NOW)
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=5))
    clock = [NOW + timedelta(minutes=6)]
    patch_clock(monkeypatch, clock)

    def fail_heartbeat(_store: Store, _now: datetime) -> None:
        raise sqlite3.OperationalError

    monkeypatch.setattr(worker_module, "record_success", fail_heartbeat)
    # When / Then: 成交、状态及日志隔离均不受心跳故障影响。
    Worker(engine).tick()
    persisted = engine.store.read()
    assert persisted.orders[0].id == placed.id
    assert persisted.orders[0].status == "filled"
    assert len(persisted.ledger) == 2
    assert "后台心跳记录失败" in caplog.text


def test_cleanup_keeps_thirty_day_boundary_and_removes_older(engine: Engine) -> None:
    # Given: 一条超过 30 天、一条恰好 30 天的记录。
    now = datetime.fromisoformat("2026-11-28T04:10:00+00:00")
    old = now - timedelta(days=30, seconds=1)
    boundary = now - timedelta(days=30)
    insert_gap(engine.store, "old", old - timedelta(minutes=10), old, ["US"])
    insert_gap(engine.store, "boundary", boundary - timedelta(minutes=10), boundary, ["CN"])
    record_success(engine.store, now)
    # Then: 清理与首次基线在同一事务完成，边界保留。
    assert [row[0] for row in raw_gaps(engine.store)] == ["boundary"]
    assert heartbeat(engine.store) == now


def test_gap_and_heartbeat_roll_back_together_when_cleanup_fails(engine: Engine) -> None:
    # Given: 一个可产生新 gap 的基线和会让 30 天清理失败的受控触发器。
    started = datetime.fromisoformat("2026-10-29T02:00:00+00:00")
    record_success(engine.store, started)
    expired = started - timedelta(days=31)
    insert_gap(engine.store, "expired", expired - timedelta(minutes=10), expired, ["CN"])
    with engine.store.transaction() as connection:
        _ = connection.execute(
            "CREATE TRIGGER controlled_cleanup_failure BEFORE DELETE ON worker_gaps BEGIN SELECT RAISE(ABORT,'x'); END"
        )
    ended = started + timedelta(minutes=10)
    # When / Then: 任一步失败，gap、心跳和清理整体回滚。
    with pytest.raises(sqlite3.Error):
        record_success(engine.store, ended)
    assert heartbeat(engine.store) == started
    assert [row[0] for row in raw_gaps(engine.store)] == ["expired"]


def test_s5_lunch_break_has_no_intersection() -> None:
    # Given: A 股与港股均在午休、美股尚未开盘的 30 分钟。
    assert (
        gap_markets(
            datetime.fromisoformat("2026-10-29T04:10:00+00:00"),
            datetime.fromisoformat("2026-10-29T04:40:00+00:00"),
        )
        == []
    )


def test_s5_half_day_close_is_real_boundary() -> None:
    # Given: 港股圣诞前夕与美股感恩节后半日市。
    # Then: 收盘前有交集，收盘后不虚构下午交易段。
    assert gap_markets(
        datetime.fromisoformat("2026-12-24T03:50:00+00:00"),
        datetime.fromisoformat("2026-12-24T04:10:00+00:00"),
    ) == ["HK"]
    assert (
        gap_markets(
            datetime.fromisoformat("2026-12-24T04:00:00+00:00"),
            datetime.fromisoformat("2026-12-24T04:30:00+00:00"),
        )
        == []
    )
    assert gap_markets(
        datetime.fromisoformat("2026-11-27T17:50:00+00:00"),
        datetime.fromisoformat("2026-11-27T18:10:00+00:00"),
    ) == ["US"]
    assert (
        gap_markets(
            datetime.fromisoformat("2026-11-27T18:00:00+00:00"),
            datetime.fromisoformat("2026-11-27T18:30:00+00:00"),
        )
        == []
    )


def test_s5_cn_application_cutoff_is_1457() -> None:
    # Given: A 股 14:57 前后各六分钟，港股仍交易用于对照。
    # Then: A 股 14:57 后不再计入，港股不受影响。
    assert gap_markets(
        datetime.fromisoformat("2026-10-29T06:56:00+00:00"),
        datetime.fromisoformat("2026-10-29T07:01:00+00:00"),
    ) == ["CN", "HK"]
    assert gap_markets(
        datetime.fromisoformat("2026-10-29T06:57:00+00:00"),
        datetime.fromisoformat("2026-10-29T07:10:00+00:00"),
    ) == ["HK"]


def test_s5_uncovered_calendar_is_unknown_not_closed() -> None:
    # Given: A 股 2028 日历尚未覆盖，港美股当天（周六）也无交易时段交集。
    # Then: 不把覆盖缺失误判成无中断。
    assert gap_markets(
        datetime.fromisoformat("2028-01-01T02:00:00+00:00"),
        datetime.fromisoformat("2028-01-01T02:10:00+00:00"),
    ) == ["unknown"]


def test_health_counts_all_recent_gaps_but_returns_latest_five(
    engine: Engine, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 七天边界内七条、八天前一条，时长含不足一分钟的墙钟尾数。
    now = datetime.fromisoformat("2026-11-28T04:10:00+00:00")
    for index in range(7):
        ended = now - timedelta(days=index)
        insert_gap(engine.store, f"recent-{index}", ended - timedelta(minutes=61, seconds=30), ended, ["US"])
    old = now - timedelta(days=8)
    insert_gap(engine.store, "old", old - timedelta(minutes=10), old, ["CN"])
    monkeypatch.setattr(MarketClock, "current", now)
    # When: 同时读取查询函数和鉴权 HTTP 健康接口。
    count, direct = gap_summary(engine.store, now)
    response = api.get("/api/health")
    health = HealthView.model_validate_json(response.content)
    # Then: 计数不截断、列表最多五条且按 ended_at 倒序；边界包含恰好七天。
    assert response.status_code == 200
    assert count == health.gap_count_7d == 7
    assert len(direct) == len(health.recent_gaps) == 5
    assert health.recent_gaps[0].ended_at == now
    assert health.recent_gaps[-1].ended_at == now - timedelta(days=4)
    assert health.recent_gaps[0].duration_minutes == 61
    assert health.recent_gaps[0].markets == ["US"]


def test_health_new_fields_default_empty_for_old_payload() -> None:
    # Given: 不含新增字段的旧健康响应。
    health = HealthView.model_validate({"plan_worker": {"status": "running"}})
    # Then: 模型向后兼容为空列表与零计数。
    assert health.recent_gaps == []
    assert health.gap_count_7d == 0


def test_health_endpoint_returns_empty_gaps_on_new_database(engine: Engine) -> None:
    # Given / When: 新隔离库只创建表，从未写 gap。
    with TestClient(create_app(engine, PASSWORD, run_worker=False)) as client:
        _ = client.post("/api/auth/login", json={"password": PASSWORD})
        health = HealthView.model_validate_json(client.get("/api/health").content)
    # Then: 接口显式返回默认空字段。
    assert health.recent_gaps == []
    assert health.gap_count_7d == 0


def test_health_still_reports_worker_when_gap_records_unreadable(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(*_: object) -> tuple[int, list[object]]:
        raise sqlite3.OperationalError

    monkeypatch.setattr("app.asset_routes.gap_summary", unreadable)
    response = api.get("/api/health")
    assert response.status_code == 200
    health = HealthView.model_validate_json(response.content)
    assert health.gap_count_7d == 0
    assert health.recent_gaps == []
