"""实际 HTTP 安全边界，转发头不能替代可信代理解析。"""

import pytest
from fastapi.testclient import TestClient

from app.engine import Engine
from app.main import create_app
from app.models import DomainError
from app.security import AccessConfig
from tests.test_api import PASSWORD


def test_private_https_cookie_and_origin(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "phone.private.example,localhost")
    with TestClient(create_app(engine, PASSWORD, run_worker=False), base_url="https://phone.private.example") as client:
        login = client.post(
            "/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "https://phone.private.example"}
        )
        assert login.status_code == 200
        assert "Secure" in login.headers["set-cookie"]
        assert client.get("/api/portfolio").status_code == 200
        assert (
            client.post(
                "/api/watchlist", json={"symbol": "AAPL"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.get("/api/portfolio", headers={"Host": "evil.example"}).status_code == 400


def test_untrusted_forwarded_proto_does_not_enable_remote_http(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "phone.private.example,localhost")
    with TestClient(create_app(engine, PASSWORD, run_worker=False), base_url="http://phone.private.example") as client:
        response = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-Forwarded-Proto": "https"})
        assert response.status_code == 403
    with TestClient(create_app(engine, PASSWORD, run_worker=False), base_url="http://localhost") as local:
        login = local.post("/api/auth/login", json={"password": PASSWORD})
        assert login.status_code == 200
        assert "Secure" not in login.headers["set-cookie"]


def test_wildcard_hosts_and_remote_http_origin_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "*")
    with pytest.raises(DomainError):
        _ = AccessConfig.from_env()
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("PAPER_ALLOWED_ORIGINS", "http://evil.example")
    with pytest.raises(DomainError):
        _ = AccessConfig.from_env()
