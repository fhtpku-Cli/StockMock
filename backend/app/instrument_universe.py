"""人工核对的美股扩展元数据；日期是已上市证据上界，不冒充精确 IPO 日期。"""

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    """发行方证据与交易所当前代码交叉核对，行情能力另做真实导入验证。"""

    symbol: str
    name: str
    kind: Literal["stock", "ETF"]
    metadata_source: str
    listed_on_or_before: date


_SECTOR_SOURCE: Final = (
    "https://www.ssga.com/library-content/products/fund-docs/etfs/apac/jp/spdr-product-list-japan.pdf"
)
_MATURE: Final = date(2025, 12, 31)
US_EXPANSION: Final = (
    CoverageEntry(
        "COST", "开市客", "stock", "https://investor.costco.com/resources/investor-faqs/default.aspx", date(1985, 12, 5)
    ),
    CoverageEntry(
        "JPM",
        "摩根大通",
        "stock",
        "https://www.jpmorganchase.com/ir/news/2025/jpmorganchase-plans-dividend-increase",
        date(2025, 7, 1),
    ),
    CoverageEntry(
        "V",
        "Visa",
        "stock",
        "https://usa.visa.com/about-visa/newsroom/press-releases.releaseId.1119995.html",
        date(2008, 3, 19),
    ),
    CoverageEntry(
        "MA",
        "万事达",
        "stock",
        "https://investor.mastercard.com/files/doc_financials/2024/q4/MA-12-31-2024-10-K-as-filed-with-exhibits.pdf",
        date(2024, 12, 31),
    ),
    CoverageEntry(
        "WMT", "沃尔玛", "stock", "https://stock.walmart.com/stock-data/stock-split-history", date(1970, 10, 1)
    ),
    CoverageEntry(
        "XOM",
        "埃克森美孚",
        "stock",
        "https://investor.exxonmobil.com/sec-filings/all-sec-filings/content/0000034088-26-000093/xom-20260630.htm",
        date(2026, 7, 2),
    ),
    CoverageEntry(
        "JNJ", "强生", "stock", "https://www.jnj.com/download/johnson-johnson-2025-annual-report", date(2025, 12, 28)
    ),
    CoverageEntry(
        "PG",
        "宝洁",
        "stock",
        "https://www.sec.gov/Archives/edgar/data/80424/000008042425000076/pg-20250630.htm",
        date(2025, 6, 30),
    ),
    CoverageEntry(
        "KO",
        "可口可乐",
        "stock",
        "https://investors.coca-colacompany.com/filings-reports/annual-filings-10-k/content/0001628280-26-010047/ko-20251231.htm",
        _MATURE,
    ),
    CoverageEntry(
        "DIS",
        "迪士尼",
        "stock",
        "https://thewaltdisneycompany.com/app/uploads/2025/01/2024-Annual-Report.pdf",
        date(2024, 9, 28),
    ),
    CoverageEntry(
        "VOO",
        "Vanguard标普500ETF",
        "ETF",
        "https://workplace.vanguard.com/assets/corp/fund_communications/pdf_publish/us-products/fact-sheet/F0968.pdf",
        _MATURE,
    ),
    CoverageEntry("VTI", "Vanguard美国全市场ETF", "ETF", "https://fund-docs.vanguard.com/F0970.pdf", _MATURE),
    CoverageEntry(
        "IVV", "iShares标普500ETF", "ETF", "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf", _MATURE
    ),
    CoverageEntry("SCHB", "Schwab美国宽基ETF", "ETF", "https://www.schwabassetmanagement.com/products/schb", _MATURE),
    CoverageEntry("SCHX", "Schwab美国大盘ETF", "ETF", "https://www.schwabassetmanagement.com/products/schx", _MATURE),
    *(
        CoverageEntry(symbol, name, "ETF", _SECTOR_SOURCE, _MATURE)
        for symbol, name in (
            ("XLK", "SPDR科技ETF"),
            ("XLF", "SPDR金融ETF"),
            ("XLE", "SPDR能源ETF"),
            ("XLV", "SPDR医疗ETF"),
            ("XLI", "SPDR工业ETF"),
            ("XLP", "SPDR必需消费ETF"),
            ("XLY", "SPDR可选消费ETF"),
            ("XLU", "SPDR公用事业ETF"),
            ("XLB", "SPDR材料ETF"),
        )
    ),
)
