"""后端生命周期任务；不依赖浏览器，失败仍执行截止超时释放。"""

import logging
from datetime import UTC, datetime
from typing import final

import anyio

from app.accounting import ACTIVE
from app.engine import Engine
from app.marketdata import MarketDataError
from app.models import WorkerView


@final
class Worker:
    """独立于浏览器的行情处理循环。"""

    def __init__(self, engine: Engine) -> None:
        """保存依赖并初始化本实例的状态。"""
        self.engine = engine
        self.view = WorkerView()

    def tick(self) -> None:
        """读取活动订单并处理行情或数据超时。"""
        now = datetime.now(UTC)
        failures: list[str] = []
        symbols = {o.symbol for o in self.engine.store.read().orders if o.status in ACTIVE}
        for symbol in symbols:
            samples = ()
            try:
                samples = self.engine.provider.intraday(symbol)
            except MarketDataError as error:
                failures.append(error.code)
                logging.getLogger(__name__).warning("行情获取失败 symbol=%s code=%s", symbol, error.code)
            self.engine.process(symbol, samples, now)
        self.view = WorkerView(last_run=now, status="行情异常，按截止规则处理" if failures else "running")

    async def run(self) -> None:
        """持续后台处理，应用关闭时由任务组取消。"""
        while True:
            await anyio.to_thread.run_sync(self.tick)
            await anyio.sleep(60)
