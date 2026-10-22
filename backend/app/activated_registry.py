"""实例隔离的已核验交易规则，成功核验后一次事务持久化。"""

import logging
from concurrent.futures import Future
from threading import Lock
from typing import final

from app.admission_sources import verify_candidate
from app.catalog import CATALOG, from_rule
from app.discovery import Discovery
from app.instrument_registry import catalog_rules, validate_rule
from app.instrument_rules import InstrumentRule
from app.marketdata import Bar, FxRate, MarketDataClient, MarketDataError, Quote
from app.models import DomainError, Instrument
from app.storage import Store, read_rows


@final
class ActivatedRegistry:
    """维护本实例规则快照及正在核验的任务，合并同证券并发请求。"""

    def __init__(self, store: Store, catalog: tuple[Instrument, ...] = CATALOG) -> None:
        """载入已经成功核验的完整规则快照。"""
        self.store = store
        self.catalog = catalog
        self.lock = Lock()
        self.pending: dict[str, Future[Instrument]] = {}
        with store.transaction() as connection:
            rows = read_rows(connection, "SELECT data FROM activated_instruments ORDER BY symbol")
        self.rules = tuple(validate_rule(InstrumentRule.model_validate_json(row[0])) for row in rows)

    def resolve(self, symbol: str) -> InstrumentRule:
        """基础及配置优先，实例目录覆盖展示字段，持久规则仅补齐新证券。"""
        item = next((item for item in self.catalog if item.symbol == symbol), None)
        candidates = (*catalog_rules(), *self.rules) if item is not None else self.rules
        rule = next((rule for rule in candidates if rule.symbol == symbol), None)
        if rule is None:
            raise MarketDataError("unsupported_symbol", symbol)
        if item is not None:
            return validate_rule(InstrumentRule.model_validate({**rule.model_dump(), **item.model_dump()}))
        return rule

    def enable(self, symbol: str, discovery: Discovery) -> Instrument:
        """并发共享成功或失败；失败可在后续用户动作重新核验。"""
        with self.lock:
            existing = next((rule for rule in self.rules if rule.symbol == symbol), None)
            if existing is not None:
                return from_rule(self.resolve(symbol))
            pending = self.pending.get(symbol)
            owner = pending is None
            if pending is None:
                pending = Future[Instrument]()
                self.pending[symbol] = pending
        if not owner:
            return pending.result()
        try:
            result = self._activate(symbol, discovery)
        except BaseException as error:  # noqa: BROAD_EXCEPT_OK
            # 同步任务边界必须唤醒全部等待者；异常原样重抛，不降级或吞掉错误。
            logging.getLogger(__name__).warning("证券启用失败 symbol=%s type=%s", symbol, type(error).__name__)
            pending.set_exception(error)
            raise
        else:
            pending.set_result(result)
            return result
        finally:
            with self.lock:
                del self.pending[symbol]

    def _activate(self, symbol: str, discovery: Discovery) -> Instrument:
        """核验完成后原子保存规则；失败不会留下可交易半成品。"""
        item = discovery.lookup(symbol, self.store)
        rule = validate_rule(verify_candidate(item, discovery))
        if rule.symbol != symbol or not rule.tradable or rule.market == "HK":
            raise DomainError("observation_only", "该证券暂不支持模拟交易")
        with self.store.transaction() as connection:
            _ = connection.execute(
                "INSERT INTO activated_instruments VALUES(?,?) ON CONFLICT(symbol) DO NOTHING",
                (rule.symbol, rule.model_dump_json()),
            )
            rows = read_rows(connection, "SELECT data FROM activated_instruments WHERE symbol=?", (symbol,))
            rule = validate_rule(InstrumentRule.model_validate_json(rows[0][0]))
        with self.lock:
            self.rules = (*self.rules, rule)
        return from_rule(rule)


@final
class RuleProvider:
    """把交易层代码解析为本实例规则，再交由真实行情适配器处理。"""

    def __init__(self, registry: ActivatedRegistry, client: MarketDataClient) -> None:
        """绑定本实例规则及真实行情客户端。"""
        self.registry = registry
        self.client = client

    def quote(self, symbol: str) -> Quote:
        """获取已核验规则的估值快照。"""
        return self.client.quote(self.registry.resolve(symbol))

    def intraday(self, symbol: str) -> tuple[Quote, ...]:
        """获取已核验规则的完整分钟线。"""
        return self.client.intraday(self.registry.resolve(symbol))

    def history(self, symbol: str) -> tuple[Bar, ...]:
        """获取已核验规则的未复权日线。"""
        return self.client.history(self.registry.resolve(symbol))

    def fx_rates(self) -> tuple[FxRate, ...]:
        """复用真实汇率入口。"""
        return self.client.fx_rates()
