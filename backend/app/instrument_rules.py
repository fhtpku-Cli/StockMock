"""逐项核验的证券规则；目录扩展必须提供完整规则，不能只凭代码猜测。"""

from datetime import date
from decimal import Decimal
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.instrument_universe import US_EXPANSION
from app.marketdata_models import Currency


class InstrumentRule(BaseModel):
    """来源及核验日期与交易参数一起保存，港股接入能力单独核验。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    symbol: str = Field(pattern=r"^[A-Z0-9.]{1,15}$")
    name: str = Field(min_length=1, max_length=80)
    provider_code: str = Field(pattern=r"^(sh|sz|hk|us)[A-Za-z0-9.]{1,12}$")
    market: Literal["CN", "HK", "US"]
    currency: Currency
    kind: Literal["stock", "ETF"]
    lot_size: int = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    settlement_days: int = Field(ge=0, le=1)
    tradable: bool
    restriction: str = Field(min_length=1)
    price_limit_ratio: Decimal | None = Field(default=None, gt=0, le=Decimal(".2"))
    min_buy_quantity: int | None = Field(default=None, gt=0)
    quantity_step: int | None = Field(default=None, gt=0)
    tick_rule: Literal["fixed", "HK", "US"] = "fixed"
    rule_source: str = Field(min_length=1)
    verified_on: date


_CN_STOCKS: Final = (
    ("600519.SS", "贵州茅台", "sh600519"),
    ("000001.SZ", "平安银行", "sz000001"),
    ("600036.SS", "招商银行", "sh600036"),
    ("601318.SS", "中国平安", "sh601318"),
    ("600900.SS", "长江电力", "sh600900"),
    ("000333.SZ", "美的集团", "sz000333"),
    ("601398.SS", "工商银行", "sh601398"),
    ("600030.SS", "中信证券", "sh600030"),
)
_CN_ETFS: Final = (
    ("510300.SS", "沪深300ETF", "sh510300", ".1"),
    ("510050.SS", "上证50ETF", "sh510050", ".1"),
    ("510500.SS", "中证500ETF", "sh510500", ".1"),
    ("159919.SZ", "沪深300ETF嘉实", "sz159919", ".1"),
    ("159915.SZ", "创业板ETF", "sz159915", ".2"),
    ("588000.SS", "科创50ETF", "sh588000", ".2"),
    ("512100.SS", "中证1000ETF", "sh512100", ".1"),
    ("159949.SZ", "创业板50ETF", "sz159949", ".2"),
)
_HK: Final[tuple[tuple[str, str, str, int, Literal["stock", "ETF"]], ...]] = (
    ("0700.HK", "腾讯控股", "hk00700", 100, "stock"),
    ("9988.HK", "阿里巴巴", "hk09988", 100, "stock"),
    ("3690.HK", "美团", "hk03690", 100, "stock"),
    ("1810.HK", "小米集团", "hk01810", 200, "stock"),
    ("0005.HK", "汇丰控股", "hk00005", 400, "stock"),
    ("0941.HK", "中国移动", "hk00941", 500, "stock"),
    ("2800.HK", "盈富基金", "hk02800", 500, "ETF"),
    ("3033.HK", "南方恒生科技ETF", "hk03033", 200, "ETF"),
)
_US: Final[tuple[tuple[str, str, Literal["stock", "ETF"]], ...]] = (
    ("AAPL", "苹果", "stock"),
    ("MSFT", "微软", "stock"),
    ("NVDA", "英伟达", "stock"),
    ("AMZN", "亚马逊", "stock"),
    ("GOOGL", "谷歌", "stock"),
    ("META", "Meta", "stock"),
    ("TSLA", "特斯拉", "stock"),
    ("AMD", "超威半导体", "stock"),
    ("SPY", "标普500ETF", "ETF"),
    ("QQQ", "纳斯达克100ETF", "ETF"),
    ("IWM", "罗素2000ETF", "ETF"),
    ("DIA", "道琼斯ETF", "ETF"),
    *((entry.symbol, entry.name, entry.kind) for entry in US_EXPANSION),
)
_VERIFIED: Final = date(2026, 10, 22)
_CN_RESTRICTION: Final = "仅成熟普通A股及逐项核验的境内股票ETF；排除风险警示、退市及上市初期；公司行动人工核对"
_CN_SOURCE: Final = "https://www.sse.com.cn/lawandrules/sselawsrules/trade/universal/"

CATALOG_RULES: Final = (
    *(
        InstrumentRule(
            symbol=s,
            name=n,
            provider_code=c,
            market="CN",
            currency="CNY",
            kind="stock",
            lot_size=100,
            tick_size=Decimal(".01"),
            settlement_days=1,
            tradable=True,
            restriction=_CN_RESTRICTION,
            price_limit_ratio=Decimal(".1"),
            rule_source=_CN_SOURCE,
            verified_on=_VERIFIED,
        )
        for s, n, c in _CN_STOCKS
    ),
    *(
        InstrumentRule(
            symbol=s,
            name=n,
            provider_code=c,
            market="CN",
            currency="CNY",
            kind="ETF",
            lot_size=100,
            tick_size=Decimal(".001"),
            settlement_days=1,
            tradable=True,
            restriction=_CN_RESTRICTION,
            price_limit_ratio=Decimal(limit),
            rule_source=_CN_SOURCE,
            verified_on=_VERIFIED,
        )
        for s, n, c, limit in _CN_ETFS
    ),
    *(
        InstrumentRule(
            symbol=s,
            name=n,
            provider_code=c,
            market="HK",
            currency="HKD",
            kind=kind,
            lot_size=lot,
            tick_size=Decimal(".001"),
            settlement_days=0,
            tradable=False,
            tick_rule="HK",
            restriction="待本人配置本机 Futu OpenD 并核验真实分钟行情；手数已按港交所当日主数据核验",
            rule_source="https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx",
            verified_on=_VERIFIED,
        )
        for s, n, c, lot, kind in _HK
    ),
    *(
        InstrumentRule(
            symbol=s,
            name=n,
            provider_code=f"us{s}",
            market="US",
            currency="USD",
            kind=kind,
            lot_size=1,
            tick_size=Decimal(".01"),
            settlement_days=0,
            tradable=True,
            tick_rule="US",
            restriction="新浪五分钟样本，实际延迟以行情时间为准；常规时段整股，≥1美元仅支持0.01报价网格；公司行动人工核对，排除杠杆/反向ETF",
            rule_source=next(
                (entry.metadata_source for entry in US_EXPANSION if entry.symbol == s),
                "https://www.sec.gov/newsroom/press-releases/2024-137",
            ),
            verified_on=_VERIFIED,
        )
        for s, n, kind in _US
    ),
)
