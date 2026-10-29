"""后端生命周期任务；不依赖浏览器，失败仍执行截止超时释放。"""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, final

import anyio

from app.accounting import ACTIVE
from app.engine import Engine
from app.marketdata import MarketDataError
from app.matching import validate_batch
from app.models import DomainError, WorkerView
from app.snapshots import capture

if TYPE_CHECKING:
    from collections.abc import Mapping


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
                failures.append(type(error).__name__)
                logging.getLogger(__name__).warning("订单处理失败 symbol=%s type=%s", symbol, type(error).__name__)
        try:
            capture(self.engine)
        except (DomainError, sqlite3.OperationalError) as error:
            failures.append(type(error).__name__)
            logging.getLogger(__name__).warning("资产观察失败 type=%s", type(error).__name__)
        self.view = WorkerView(last_run=datetime.now(UTC), status="行情异常，按截止规则处理" if failures else "running")
        self.failures = MappingProxyType(self.failed_since.copy())

    async def run(self) -> None:
        """持续后台处理，应用关闭时由任务组取消。"""
        while True:
            await anyio.to_thread.run_sync(self.tick)
            await anyio.sleep(60)
