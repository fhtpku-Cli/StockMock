"""基础目录接纳旧动态证券时，展示、受理及行情必须解析相同规则。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.activated_registry import RuleProvider
from app.catalog import CATALOG, from_rule, instrument
from app.engine import Engine
from app.main import create_app
from app.storage import Store
from tests.admission_support import RuleClient
from tests.test_admission import inject, isolated, security_rule
from tests.test_api import PASSWORD


@pytest.mark.parametrize("symbol", ["600000.SS", "600519.SS"])
def test_rebuilt_base_catalog_overrides_old_persisted_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symbol: str,
) -> None:
    # Given: 旧版持久规则；新版基础或配置目录重新收录证券并暂停。
    old = security_rule(symbol)
    inject(monkeypatch, old)
    engine = isolated(tmp_path)
    with engine.store.transaction() as connection:
        _ = connection.execute("INSERT INTO activated_instruments VALUES(?,?)", (symbol, old.model_dump_json()))
    corrected = old.model_copy(update={"name": "核对后的证券名称", "tradable": False, "restriction": "规则重新核对中"})
    catalog = (*(item for item in CATALOG if item.symbol != symbol), from_rule(corrected))
    # When: 使用新目录重建实例，再调用真实认证HTTP及行情规则适配器。
    rebuilt = Engine(Store(engine.store.path), RuleClient(), catalog)
    provider = RuleProvider(rebuilt.registry, RuleClient())
    rebuilt.provider = provider
    with TestClient(create_app(rebuilt, PASSWORD, run_worker=False)) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert client.post(f"/api/instruments/{symbol}/enable").status_code == 409
        assert client.get(f"/api/instruments/{symbol}/quote").status_code == 200
    # Then: 展示与行情适配器持有同一套完整规则，旧持久可交易状态不能胜出。
    assert instrument(symbol, rebuilt.catalog) == from_rule(provider.registry.resolve(symbol))
    assert provider.registry.resolve(symbol) == corrected
    assert not provider.registry.resolve(symbol).tradable
