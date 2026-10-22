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
    "159901.SZ": EtfMetadata("https://www.efunds.com.cn/fund/159901_org.shtml", Decimal(".1")),
    **{
        f"{code}.SZ": EtfMetadata(f"https://www.chinaamc.com/fund/{code}/index.shtml", Decimal(limit))
        for code, limit in (
            ("159902", ".1"),
            ("159995", ".1"),
            ("159957", ".2"),
            ("159966", ".2"),
            ("159967", ".2"),
        )
    },
    **{
        f"{code}.SZ": EtfMetadata(f"https://qy.99fund.com/fundDetail/index.htm?fundId={code}", Decimal(".1"))
        for code in ("159912", "159928", "159929", "159930", "159931")
    },
    "159938.SZ": EtfMetadata("https://www.gffunds.com.cn/jjgg/flwj/202601/P020260106334888466020.pdf", Decimal(".1")),
    "159939.SZ": EtfMetadata(
        "https://dataclouds.cninfo.com.cn/sjother2/fund/2026/20260324/2c64588a31f04c7594fca45e60b8e9de.pdf",
        Decimal(".1"),
    ),
    **{
        symbol: EtfMetadata(
            "https://www.ishares.com/us/literature/product-brief/ishares-core-us-stock-product-brief-itot-ivv-ijh-ijr-en-us.pdf",
            None,
        )
        for symbol in ("ITOT", "IJH", "IJR")
    },
    "IWF": EtfMetadata("https://www.ishares.com/us/products/239706/ishares-russell-1000-growth-etf", None),
    "IWD": EtfMetadata("https://www.ishares.com/us/products/239708/ishares-russell-1000-value-etf", None),
    **{
        symbol: EtfMetadata(f"https://www.schwabassetmanagement.com/products/{symbol.lower()}", None)
        for symbol in ("SCHD", "SCHG", "SCHV", "SCHM", "SCHA")
    },
    "XLC": EtfMetadata(
        "https://www.ssga.com/us/en/individual/etfs/state-street-communication-services-select-sector-spdr-etf-xlc",
        None,
    ),
    "XBI": EtfMetadata(
        "https://www.ssga.com/library-content/products/factsheets/etfs/us/factsheet-us-en-xbi.pdf", None
    ),
    "SMH": EtfMetadata("https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/overview", None),
}
