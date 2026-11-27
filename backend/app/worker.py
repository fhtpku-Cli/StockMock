"""后端生命周期任务；不依赖浏览器，失败仍执行截止超时释放。"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, final

import anyio
from pydantic import TypeAdapter

from app.accounting import ACTIVE
from app.calendar import calendar
from app.marketdata import MarketDataError
from app.matching import validate_batch
from app.models import DomainError, Gap, GapMarket, WorkerView
from app.snapshots import capture
from app.storage import Store, read_rows

if TYPE_CHECKING:
    from collections.abc import Mapping

    from exchange_calendars import ExchangeCalendar, Timestamp

    from app.engine import Engine

Market = Literal["CN", "HK", "US"]
MARKETS: Final[tuple[Market, ...]] = ("CN", "HK", "US")
GAP_THRESHOLD: Final = timedelta(minutes=5)
GAP_RETENTION: Final = timedelta(days=30)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _session_periods(
    cal: ExchangeCalendar, session: Timestamp, market: Market
) -> tuple[tuple[datetime, datetime], ...]:
    """读取实际交易日会话，午休两侧分别相交，A 股沿用 14:57 截止。"""
    opening = cal.session_open(session).to_pydatetime()
    closing = cal.session_close(session).to_pydatetime() - (timedelta(minutes=3) if market == "CN" else timedelta(0))
    if market not in {"CN", "HK"}:
        return ((opening, closing),)
    break_start = cal.session_break_start(session).to_pydatetime()
    break_end = cal.session_break_end(session).to_pydatetime()
    missing_start, missing_end = break_start.isoformat() == "NaT", break_end.isoformat() == "NaT"
    if missing_start and missing_end:
        return ((opening, closing),)
    if missing_start or missing_end or not opening < break_start < break_end < closing:
        raise ValueError("invalid_calendar_break")
    return ((opening, break_start), (break_end, closing))


def _day_overlap(
    cal: ExchangeCalendar, market: Market, day: date, started: datetime, ended: datetime
) -> tuple[bool, bool]:
    """返回某日是否有正长度交集、该日是否无法由日历判定。"""
    first = cal.first_session.to_pydatetime().date()
    last = cal.last_session.to_pydatetime().date()
    if not first <= day <= last:
        return False, True
    try:
        session = cal.date_to_session(str(day), direction="none")
    except ValueError:
        return False, False
    except (IndexError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError):
        return False, True
    try:
        periods = _session_periods(cal, session, market)
    except (ValueError, IndexError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError):
        return False, True
    overlap = any(max(opening, started) < min(closing, ended) for opening, closing in periods)
    return overlap, False


def _market_overlap(market: Market, started: datetime, ended: datetime) -> tuple[bool, bool]:
    try:
        cal = calendar(market)
    except (ValueError, IndexError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError):
        return False, True
    day = started.astimezone(cal.tz).date()
    last_day = (ended - timedelta(microseconds=1)).astimezone(cal.tz).date()
    overlap = False
    unknown = False
    while day <= last_day:
        day_overlap, day_unknown = _day_overlap(cal, market, day, started, ended)
        overlap = overlap or day_overlap
        unknown = unknown or day_unknown
        day += timedelta(days=1)
    return overlap, unknown


def gap_markets(started_at: datetime, ended_at: datetime) -> list[GapMarket]:
    """按会话区间而非分钟采样计算中断涉及的市场。"""
    started, ended = _utc(started_at), _utc(ended_at)
    if ended <= started:
        return []
    result: list[GapMarket] = []
    unknown = False
    for market in MARKETS:
        overlap, market_unknown = _market_overlap(market, started, ended)
        if overlap:
            result.append(market)
        unknown = unknown or market_unknown
    if unknown:
        result.append("unknown")
    return result


def record_success(store: Store, now: datetime) -> None:
    """在独立写事务内原子完成 gap 判定、心跳推进和 30 天清理。"""
    now = _utc(now)
    with store.transaction() as connection:
        previous_rows = read_rows(connection, "SELECT last_success FROM worker_heartbeat WHERE id=1")
        if previous_rows:
            previous = _utc(datetime.fromisoformat(previous_rows[0][0]))
            if now - previous > GAP_THRESHOLD:
                markets = gap_markets(previous, now)
                if markets:
                    started = _iso(previous)
                    _ = connection.execute(
                        """INSERT INTO worker_gaps(id,started_at,ended_at,markets,cause)
                        VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                        (
                            started,
                            started,
                            _iso(now),
                            json.dumps(markets, separators=(",", ":")),
                            "paused_or_down",
                        ),
                    )
        _ = connection.execute(
            """INSERT INTO worker_heartbeat(id,last_success) VALUES(1,?)
            ON CONFLICT(id) DO UPDATE SET last_success=excluded.last_success""",
            (_iso(now),),
        )
        _ = connection.execute("DELETE FROM worker_gaps WHERE ended_at<?", (_iso(now - GAP_RETENTION),))


def gap_summary(store: Store, now: datetime) -> tuple[int, list[Gap]]:
    """读取最近七天的全部计数和按结束时间倒序的前五条。"""
    now = _utc(now)
    cutoff = _iso(now - timedelta(days=7))
    with store.read_transaction() as connection:
        rows = read_rows(
            connection,
            """SELECT started_at,ended_at,markets FROM worker_gaps
            WHERE ended_at>=? AND ended_at<=? ORDER BY ended_at DESC""",
            (cutoff, _iso(now)),
        )
    gaps = [
        Gap(
            started_at=datetime.fromisoformat(row[0]),
            ended_at=datetime.fromisoformat(row[1]),
            duration_minutes=max(
                0,
                int((datetime.fromisoformat(row[1]) - datetime.fromisoformat(row[0])).total_seconds() // 60),
            ),
            markets=TypeAdapter(list[GapMarket]).validate_json(row[2]),
        )
        for row in rows
    ]
    return len(gaps), gaps[:5]


@final
class Worker:
    """独立于浏览器的行情处理循环。"""

    def __init__(self, engine: Engine) -> None:
        """保存依赖并初始化本实例的状态。"""
        self.engine = engine
        self.view = WorkerView()
        self.failed_since: dict[str, datetime] = {}
        self.failures: Mapping[str, datetime] = MappingProxyType({})
        self.last_tick: datetime | None = None

    def tick(self) -> None:
        """读取活动订单并处理行情或数据超时。"""
        now = datetime.now(UTC)
        if self.last_tick is None or now - self.last_tick > timedelta(minutes=5) or now < self.last_tick:
            self.failed_since.clear()
        self.last_tick = now
        failures: list[str] = []
        try:
            symbols = {o.symbol for o in self.engine.store.read().orders if o.status in ACTIVE}
        except (DomainError, sqlite3.OperationalError) as error:
            self.view = WorkerView(last_run=now, status="账户暂不可读，下轮重试")
            self.failures = MappingProxyType(self.failed_since.copy())
            logging.getLogger(__name__).warning("账户读取失败 type=%s", type(error).__name__)
            return
        self.failed_since = {symbol: since for symbol, since in self.failed_since.items() if symbol in symbols}
        processing_failed = False
        for symbol in symbols:
            samples = ()
            try:
                samples = self.engine.provider.intraday(symbol)
                validate_batch(samples)
                _ = self.failed_since.pop(symbol, None)
            except MarketDataError as error:
                samples = ()
                _ = self.failed_since.setdefault(symbol, now)
                failures.append(error.code)
                logging.getLogger(__name__).warning("行情获取失败 symbol=%s code=%s", symbol, error.code)
            except Exception as error:  # noqa: BLE001
                samples = ()
                _ = self.failed_since.setdefault(symbol, now)
                failures.append(type(error).__name__)
                logging.getLogger(__name__).warning("行情获取意外失败 symbol=%s type=%s", symbol, type(error).__name__)
            try:
                completed = datetime.now(UTC)
                failed_at = self.failed_since.get(symbol)
                self.engine.process(
                    symbol,
                    samples,
                    completed,
                    allow_timeout=failed_at is None or completed - failed_at >= timedelta(minutes=20),
                )
            except (DomainError, sqlite3.OperationalError) as error:
                processing_failed = True
                failures.append(type(error).__name__)
                logging.getLogger(__name__).warning("订单处理失败 symbol=%s type=%s", symbol, type(error).__name__)
        try:
            capture(self.engine)
        except (DomainError, sqlite3.OperationalError) as error:
            processing_failed = True
            failures.append(type(error).__name__)
            logging.getLogger(__name__).warning("资产观察失败 type=%s", type(error).__name__)
        completed = datetime.now(UTC)
        self.view = WorkerView(last_run=completed, status="行情异常，按截止规则处理" if failures else "running")
        self.failures = MappingProxyType(self.failed_since.copy())
        if not processing_failed:
            self._record_success(completed)

    def _record_success(self, now: datetime) -> None:
        """心跳故障与交易结果隔离，日志不携带数据库或供应商敏感内容。"""
        try:
            record_success(self.engine.store, now)
        except Exception as error:  # noqa: BLE001 任何心跳故障都不能改变已完成的交易处理。
            logging.getLogger(__name__).warning("后台心跳记录失败 type=%s", type(error).__name__)

    async def run(self) -> None:
        """持续后台处理，应用关闭时由任务组取消。"""
        while True:
            try:
                await anyio.to_thread.run_sync(self.tick)
            except Exception as error:  # noqa: BLE001
                self.view = WorkerView(last_run=datetime.now(UTC), status="交易处理暂不可用，下轮重试")
                logging.getLogger(__name__).warning("交易处理下轮重试 type=%s", type(error).__name__)
            await anyio.sleep(60)
