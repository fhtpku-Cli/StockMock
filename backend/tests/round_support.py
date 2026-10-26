"""轮次竞争测试共用受控行情与真实 HTTP 操作，不连接正式账户。"""

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import ClassVar
from uuid import uuid4

import httpx2
import pytest

from app import asset_routes, record_routes, trading_routes
from app.engine import Engine
from app.marketdata import Bar, FxRate, Quote
from app.models import Order, OrderInput
from tests.test_engine import NOW, ControlledProvider, request, sample


class MarketClock:
    """只在当前测试中推进路由时钟，恢复由 monkeypatch 负责。"""

    current: ClassVar[datetime] = NOW

    @staticmethod
    def now(_tz: tzinfo | None = None) -> datetime:
        return MarketClock.current


def set_clock(monkeypatch: pytest.MonkeyPatch, now: datetime = NOW) -> None:
    monkeypatch.setattr(MarketClock, "current", now)
    for module in (record_routes, asset_routes, trading_routes):
        monkeypatch.setattr(module, "datetime", MarketClock)


@dataclass(frozen=True, slots=True)
class BarrierProvider:
    """只阻塞第一次指定供应商读取；后续设备可正常完成业务。"""

    base: ControlledProvider = field(default_factory=ControlledProvider)
    pause_fx: bool = False
    entered: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)

    def wait_once(self) -> None:
        if not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(timeout=5), "测试未释放供应商读取"

    def quote(self, symbol: str) -> Quote:
        return self.base.quote(symbol)

    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        if not self.pause_fx:
            self.wait_once()
        return self.base.intraday(symbol)

    def history(self, symbol: str) -> tuple[Bar, ...]:
        return self.base.history(symbol)

    def fx_rates(self) -> tuple[FxRate, ...]:
        if self.pause_fx:
            self.wait_once()
        return self.base.fx_rates()


def reset_http(http: httpx2.Client, url: str) -> None:
    response = http.post(url + "/api/rounds/reset", json={"request_id": str(uuid4()), "confirmation": "重新开始"})
    assert response.status_code == 200, response.text


def retry_in_new_process(path: Path, payload: OrderInput) -> Order:
    """独立 Python 进程从原库重载请求，排除内存状态保留的假通过。"""
    code = """import sys
from pathlib import Path
from app.engine import Engine
from app.models import OrderInput
from app.storage import Store
from tests.test_engine import NOW, ControlledProvider
engine = Engine(Store(Path(sys.argv[1])), ControlledProvider())
print(engine.place(OrderInput.model_validate_json(sys.stdin.read()), NOW).model_dump_json())
"""
    result = subprocess.run(  # noqa: S603 — 固定 Python 代码，只访问本测试的临时数据库。
        [sys.executable, "-c", code, str(path)],
        input=payload.model_dump_json(),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        cwd=Path(__file__).resolve().parents[1],
    )
    return Order.model_validate_json(result.stdout)


def earn_dollars(engine: Engine, http: httpx2.Client, url: str, now: datetime) -> None:
    """通过真实下单 API 和撮合得到美元，禁止直接注入现金。"""
    MarketClock.current = now
    buy_body = request().model_copy(update={"symbol": "AAPL", "quantity": 1})
    response = http.post(url + "/api/orders", json=buy_body.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    buy = Order.model_validate_json(response.content)
    engine.process(buy.symbol, (sample(engine, buy, "99"),), now + timedelta(minutes=2))
    MarketClock.current = now + timedelta(minutes=3)
    sell_body = buy_body.model_copy(update={"request_id": str(uuid4()), "side": "sell"})
    response = http.post(url + "/api/orders", json=sell_body.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    sell = Order.model_validate_json(response.content)
    engine.process(sell.symbol, (sample(engine, sell, "101"),), now + timedelta(minutes=5))
    assert engine.store.read().cash["USD"] == Decimal("100.95")
