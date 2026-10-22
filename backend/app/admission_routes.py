"""按需核验与持久启用模拟交易的认证接口。"""

from typing import Final

from fastapi import Depends, FastAPI

from app.auth import Auth
from app.discovery import Discovery
from app.engine import Engine
from app.marketdata import MarketDataError
from app.models import DomainError, Instrument

_ADMISSION_REASONS: Final = {
    "unverified_etf_rules": "该 ETF 的投资类别及交易规则尚未核验，暂时仅供观察",
    "unsupported_provider_symbol": "此特殊证券代码尚无已核验的行情映射，暂不支持模拟交易",
    "unverified_instrument_maturity": "现有历史行情无法确认已上市至少 90 天，暂时仅供观察",
    "unverified_price_limit": "实际涨跌幅与支持的交易规则尚未核对一致，暂时仅供观察",
    "unsupported_instrument_status": "该证券属于风险警示、退市或其他不支持状态，暂不支持模拟交易",
    "unsupported_instrument_class": "该证券类别不在当前模拟交易支持范围，暂时仅供观察",
    "market_observation_only": "该市场目前仅供观察，不支持模拟交易",
    "instrument_identity_changed": "实时搜索未能确认相同证券身份及类别，请重新搜索后再试",
}


def register(app: FastAPI, engine: Engine, auth: Auth) -> None:
    """暂停和港股限制在核验前生效，成功返回完整交易参数。"""
    discovery = Discovery()

    @app.post("/api/instruments/{symbol}/enable", dependencies=[Depends(auth.require)])
    def enable(symbol: str) -> Instrument:
        symbol = symbol.strip().upper()
        if symbol in engine.paused or symbol.endswith(".HK"):
            raise DomainError("observation_only", "该证券已暂停或仅供观察")
        existing = next((item for item in engine.catalog if item.symbol == symbol), None)
        if existing is not None:
            if not existing.tradable:
                raise DomainError("observation_only", existing.restriction)
            return existing
        try:
            return engine.registry.enable(symbol, discovery)
        except MarketDataError as error:
            reason = _ADMISSION_REASONS.get(error.code)
            if reason is None:
                raise
            raise DomainError(error.code, reason) from error
