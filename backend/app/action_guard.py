"""下单事务再次检查公司行动，防止采价与调整之间的竞争。"""

import sqlite3
from datetime import datetime

from app.calendar import prior_session_day, window
from app.marketdata import Quote
from app.models import DomainError, Instrument
from app.personal_models import Adjustment
from app.storage import read_rows


def check_actions(connection: sqlite3.Connection, round_id: str, item: Instrument, quote: Quote, now: datetime) -> None:
    """只有动作之后的样本和匹配的日价格参考才能受理。"""
    actions = [
        Adjustment.model_validate_json(row[0])
        for row in read_rows(
            connection,
            "SELECT data FROM adjustments WHERE round_id=? AND symbol=?",
            (round_id, item.symbol),
        )
    ]
    if any(quote.market_time <= action.created_at for action in actions):
        raise DomainError("action_price_pending", "公司行动后尚无新的完整样本，暂停委托")
    if actions and item.market == "CN":
        opening, _ = window(item, now)
        reference_day = prior_session_day(item, opening)
        if any(reference_day < action.effective_date for action in actions):
            raise DomainError("action_reference_pending", "需等公司行动日之后可核对的前收盘价，暂停当日委托")
