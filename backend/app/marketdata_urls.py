"""腾讯未复权日线地址选择；图表与成熟性核验共用同一证券路径。"""

from typing import Literal

from app.discovery_models import ObservationInstrument
from app.instrument_rules import InstrumentRule


def tencent_history_url(item: InstrumentRule | ObservationInstrument, count: Literal[30, 252]) -> str:
    """境内证券使用原始kline接口，其余保留已有未复权day请求。"""
    if item.market == "CN":
        return f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={item.provider_code},day,,,{count}"
    return f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={item.provider_code},day,,,{count},"
