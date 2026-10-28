"""隔离证券启用场景的受控行情；不接触正式数据库或真实账户。"""

from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import override

from app.discovery_models import ObservationInstrument
from app.instrument_registry import resolve_rule
from app.instrument_rules import InstrumentRule
from app.marketdata import Bar, FxRate, MarketDataClient, Quote
from tests.test_engine import NOW


class Clock:
    current: datetime = NOW

    @staticmethod
    def now(_tz: tzinfo | None = UTC) -> datetime:
        return Clock.current


class RuleClient(MarketDataClient):
    """真正使用传入的核验规则生成受控事件，未知字符串仍拒绝。"""

    @override
    def quote(self, symbol: str | InstrumentRule | ObservationInstrument) -> Quote:
        assert not isinstance(symbol, ObservationInstrument)
        rule = resolve_rule(symbol)
        end = Clock.current.replace(minute=Clock.current.minute // 5 * 5, second=0, microsecond=0)
        return Quote(
            rule.symbol,
            Decimal(100),
            rule.currency,
            end,
            Clock.current,
            "controlled-admission",
            tradable=True,
            interval_start=end - timedelta(minutes=5),
            volume=Decimal(10000),
        )

    @override
    def intraday(self, symbol: str | InstrumentRule) -> tuple[Quote, ...]:
        return (self.quote(symbol),)

    @override
    def history(self, symbol: str | InstrumentRule | ObservationInstrument) -> tuple[Bar, ...]:
        quote = self.quote(symbol)
        return (
            Bar(
                quote.symbol,
                (Clock.current - timedelta(days=1)).date(),
                Decimal(100),
                Decimal(100),
                Decimal(100),
                Decimal(100),
                Decimal(10000),
                quote.currency,
                quote.market_time,
                quote.retrieved_at,
                quote.source,
            ),
        )

    @override
    def fx_rates(self) -> tuple[FxRate, ...]:
        return (FxRate("USD", Decimal(7), Clock.current, Clock.current, "controlled-admission"),)
