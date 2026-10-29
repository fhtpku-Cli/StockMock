"""市场支持时段的独立只读响应，不参与交易受理。"""

from datetime import UTC, date
from typing import ClassVar, Final, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

Market = Literal["CN", "HK", "US"]
MarketTimezone = Literal["Asia/Shanghai", "Asia/Hong_Kong", "America/New_York"]
Phase = Literal["supported", "before_open", "break", "unsupported_closing", "after_hours", "non_session_day", "unknown"]
TIMEZONES: Final[dict[Market, MarketTimezone]] = {
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "US": "America/New_York",
}


class SupportPeriod(BaseModel):
    """带时区、左闭右开的单个支持区间。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> Self:
        """按绝对时刻校验，允许不同时间偏移。"""
        if self.start.astimezone(UTC) >= self.end.astimezone(UTC):
            raise ValueError("invalid_period_order")
        return self


class MarketSessionView(BaseModel):
    """校验市场日期、时区、状态和区间之间的关系。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    market: Market
    as_of: AwareDatetime
    market_timezone: MarketTimezone
    market_date: date
    phase: Phase
    current_period: SupportPeriod | None
    next_period: SupportPeriod | None
    reason: Literal["calendar_unavailable"] | None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        """拒绝矛盾字段，避免将不可信响应展示为当前状态。"""
        if self.market_timezone != TIMEZONES[self.market]:
            raise ValueError("market_timezone_mismatch")
        if self.market_date != self.as_of.astimezone(ZoneInfo(self.market_timezone)).date():
            raise ValueError("market_date_mismatch")
        if self.phase == "unknown":
            if self.reason != "calendar_unavailable" or self.current_period is not None or self.next_period is not None:
                raise ValueError("invalid_unknown_state")
        elif self.reason is not None:
            raise ValueError("unexpected_reason")
        return self

    @model_validator(mode="after")
    def consistent_periods(self) -> Self:
        """按绝对时刻校验当前段、下一段与查询时刻。"""
        now = self.as_of.astimezone(UTC)
        if (self.phase == "supported") != (self.current_period is not None):
            raise ValueError("invalid_current_period")
        if self.current_period is not None and not (
            self.current_period.start.astimezone(UTC) <= now < self.current_period.end.astimezone(UTC)
        ):
            raise ValueError("current_period_excludes_query")
        if self.next_period is not None:
            if self.next_period.start.astimezone(UTC) <= now:
                raise ValueError("next_period_not_future")
            if self.current_period is not None and self.next_period.start.astimezone(
                UTC
            ) < self.current_period.end.astimezone(UTC):
                raise ValueError("overlapping_periods")
        if self.phase == "unsupported_closing" and self.market != "CN":
            raise ValueError("closing_phase_market_mismatch")
        return self
