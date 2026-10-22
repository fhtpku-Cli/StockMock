"""腾讯未复权日线地址选择；图表与成熟性核验共用同一证券路径。"""

from typing import Literal

from app.discovery_models import ObservationInstrument
from app.instrument_rules import InstrumentRule


def tencent_history_url(item: InstrumentRule | ObservationInstrument, count: Literal[30, 252]) -> str:
    """境内及港股观察日线统一原始kline接口；美股历史由独立适配器处理。"""
    return f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={item.provider_code},day,,,{count}"
