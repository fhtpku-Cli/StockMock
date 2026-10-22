"""ETF 类别与参数逐项元数据；不通过代码前缀推断投资类别、20%或T+0。"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.instrument_rules import CATALOG_RULES


@dataclass(frozen=True, slots=True)
class EtfMetadata:
    """仅收录普通股票指数 ETF；扩展必须先核对发行方或交易所资料。"""

    source: str
    price_limit_ratio: Decimal | None


_SSE_LIST: Final = "https://www.sse.com.cn/assortment/fund/list/?type=5"
_VANGUARD_LIST: Final = "https://investor.vanguard.com/investment-products/list/etfs?lang=en"
ETF_METADATA: Final = {
    **{
        row.symbol: EtfMetadata(row.rule_source, row.price_limit_ratio)
        for row in CATALOG_RULES
        if row.kind == "ETF" and row.market != "HK"
    },
    **{
        f"{code}.SS": EtfMetadata(_SSE_LIST, Decimal(".1"))
        for code in (
            "510010",
            "510020",
            "510030",
            "510040",
            "510060",
            "510180",
            "510880",
            "512010",
            "512170",
            "512660",
            "512690",
            "512800",
            "512880",
            "515000",
            "515050",
        )
    },
    **{
        symbol: EtfMetadata(_VANGUARD_LIST, None)
        for symbol in (
            "VUG",
            "VTV",
            "VYM",
            "VGT",
            "VO",
            "VB",
            "VBR",
            "VXUS",
            "VT",
            "VEA",
            "VWO",
        )
    },
    # 指向逐项已读取的发行方/交易所文件，避免只保留泛化入口。
    **{
        f"{code}.SS": EtfMetadata("https://english.sse.com.cn/access/via/eligible/", Decimal(".1"))
        for code in ("510880", "512660", "512690")
    },
    **{
        f"{code}.SS": EtfMetadata(
            "https://etf.sse.com.cn/fundtrends/c/10055544/files/ce4d5d8bcd1140beb15955f5cd345deb.pdf", Decimal(".1")
        )
        for code in ("512010", "512170", "512800", "512880")
    },
    "515000.SS": EtfMetadata(
        "https://www.sse.com.cn/disclosure/fund/announcement/c/2019-08-15/515000_20190816_1.pdf", Decimal(".1")
    ),
    "515050.SS": EtfMetadata(
        "https://www.sse.com.cn/disclosure/fund/announcement/c/2020-10-26/515050_20201027_1.pdf", Decimal(".1")
    ),
}
