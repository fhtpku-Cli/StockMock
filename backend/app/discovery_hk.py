"""用港交所每日证券清单区分股票、ETF 与非目标产品，不导入交易规则。"""

import io
import zipfile
from typing import Final, Literal
from xml.etree import ElementTree as ET

from app.marketdata_models import MarketDataError

HK_LIST_URL: Final = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
_ZIP_LIMIT: Final = 2_000_000
_XML_LIMIT: Final = 30_000_000
_NS: Final = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > _XML_LIMIT:
        raise MarketDataError("invalid_hk_catalog", "清单解压超出上限")
    raw = archive.read(name)
    if b"<!" in raw or b"\x00" in raw:
        raise MarketDataError("invalid_hk_catalog", "清单含不允许的 XML 声明")
    # 已限制解压大小，并拒绝 DTD/实体声明，解析器无外部实体输入。
    return ET.fromstring(raw)  # noqa: S314


def hk_categories(raw: bytes) -> dict[str, Literal["stock", "ETF"]]:
    """只接受官方清单中 HKD 股票与明确标识的 ETF，REIT/杠反产品不猜测。"""
    if len(raw) > _ZIP_LIMIT:
        raise MarketDataError("invalid_hk_catalog", "清单压缩包超出上限")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if (
                sum(archive.getinfo(name).file_size for name in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"))
                > _XML_LIMIT
            ):
                raise MarketDataError("invalid_hk_catalog", "清单解压总量超出上限")
            shared = ["".join(node.itertext()) for node in _xml(archive, "xl/sharedStrings.xml")]
            sheet = _xml(archive, "xl/worksheets/sheet1.xml")
        result: dict[str, Literal["stock", "ETF"]] = {}
        header_found = False
        for row in sheet.iter(f"{_NS}row"):
            cells = _cells(row, shared)
            if cells.get("A") == "Stock Code":
                header_found = (
                    cells.get("C") == "Category"
                    and cells.get("D") == "Sub-Category"
                    and cells.get("Q") == "Trading Currency"
                )
            code = cells.get("A", "")
            if not header_found or not code.isascii() or not code.isdigit() or cells.get("Q") != "HKD":
                continue
            if cells.get("C") == "Equity" and cells.get("D", "").startswith("Equity Securities"):
                result[code.zfill(5)] = "stock"
            elif cells.get("C") == "Exchange Traded Products" and cells.get("D") == "Exchange Traded Funds":
                result[code.zfill(5)] = "ETF"
        if not header_found or not result:
            raise MarketDataError("invalid_hk_catalog", "官方清单字段或类别已改变")
    except (zipfile.BadZipFile, KeyError, IndexError, ValueError, ET.ParseError) as exc:
        raise MarketDataError("invalid_hk_catalog", "港股官方清单不可解析") from exc
    return result


def _cells(row: ET.Element, shared: list[str]) -> dict[str, str]:
    cells: dict[str, str] = {}
    for cell in row:
        value = cell.find(f"{_NS}v")
        if value is not None and value.text is not None:
            text = shared[int(value.text)] if cell.get("t") == "s" else value.text
            column = cell.attrib["r"].rstrip("0123456789")
            cells[column] = text
    return cells
