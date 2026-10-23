"""独立计划观察任务；有界轮询且不占用撮合任务或行情客户端锁。"""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Final, final

import anyio

from app.calendar import is_open
from app.catalog import instrument
from app.engine import Engine
from app.marketdata import MarketDataError
from app.models import DomainError, WorkerView
from app.plan_alerts import due_alert, observe, price_alerts
from app.plans import round_plans

BATCH_SIZE: Final = 16


@final
class PlanWorker:
    """保存轮询游标，所有行情网络操作在独立线程且位于事务外。"""

    def __init__(self, engine: Engine) -> None:
        """生产 Engine 配置独立的有界缓存客户端，防止共享网络锁阻塞撮合。"""
        self.engine = engine
        self.cursor = 0
        self.view = WorkerView()

    def tick(self) -> None:
        """只查询当前活动计划证券，单来源错误不终止其他证券。"""
        now = datetime.now(UTC)
        plans = [p for p in round_plans(self.engine.store, self.engine.store.read().id) if p.status == "active"]
        for plan in plans:
            due_alert(self.engine.store, plan, now)
        if not plans:
            self.cursor = 0
            self.view = WorkerView(last_run=now, status="running")
            return
        start = self.cursor % len(plans)
        selected = (plans[start:] + plans[:start])[:BATCH_SIZE]
        self.cursor = (start + len(selected)) % len(plans)
        for plan in selected:
            sample = None
            try:
                if plan.symbol not in self.engine.paused:
                    samples = self.engine.plan_provider.intraday(plan.symbol)
                    sample = max(samples, key=lambda item: item.market_time) if samples else None
                    item = instrument(plan.symbol, self.engine.catalog)
                    if sample is not None and (
                        sample.interval_start is None
                        or not is_open(item, sample.interval_start)
                        or not is_open(item, sample.market_time - timedelta(microseconds=1))
                    ):
                        sample = None
            except (MarketDataError, DomainError) as error:
                sample = None
                logging.getLogger(__name__).warning("计划行情不可用 symbol=%s code=%s", plan.symbol, error.code)
            price_alerts(self.engine.store, plan, observe(plan, sample, datetime.now(UTC)))

        self.view = WorkerView(last_run=datetime.now(UTC), status="running")

    async def run(self) -> None:
        """与原 Worker 分离运行；线程数固定为一，不随计划数扩张。"""
        limiter = anyio.CapacityLimiter(1)
        while True:
            try:
                await anyio.to_thread.run_sync(self.tick, limiter=limiter)
            except (DomainError, sqlite3.OperationalError) as error:
                self.view = WorkerView(last_run=datetime.now(UTC), status="计划观察暂不可用，下轮重试")
                logging.getLogger(__name__).warning("计划观察下轮重试 type=%s", type(error).__name__)
            await anyio.sleep(60)
