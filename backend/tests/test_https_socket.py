"""真实 TLS 终止代理到 Uvicorn 的个人账户隔离验证。"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

import app.worker as worker_module
from app.engine import Engine
from app.main import create_app
from app.storage import Store
from app.worker import Worker
from tests.socket_support import client, server, tls_proxy
from tests.test_api import PASSWORD
from tests.test_engine import NOW, ControlledProvider, request
from tests.test_worker import MarketClock


def test_https_proxy_sessions_and_security(engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "phone.private.example,127.0.0.1,localhost")
    app = create_app(engine, PASSWORD, run_worker=False)
    with (
        server(app) as upstream,
        tls_proxy(upstream, tmp_path) as (url, context),
        client(context) as phone,
        client() as desktop,
    ):
        phone.headers["Host"] = "phone.private.example:8443"
        phone.headers["Origin"] = "https://phone.private.example:8443"
        unauthorized = phone.get(url + "/api/portfolio")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["cache-control"] == "no-store"
        login = phone.post(url + "/api/auth/login", json={"password": PASSWORD})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"]
        assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=strict"))
        assert login.headers["cache-control"] == "no-store"
        # Host 被测试域替代，因此显式保留该域 Cookie，而不是 localhost CookieJar。
        phone.headers["Cookie"] = cookie.split(";", 1)[0]
        assert desktop.post(upstream + "/api/auth/login", json={"password": PASSWORD}).status_code == 200
        mobile = phone.get(url + "/api/portfolio")
        local = desktop.get(upstream + "/api/portfolio")
        assert mobile.status_code == local.status_code == 200
        assert mobile.json()["round_id"] == local.json()["round_id"]
        assert mobile.headers["cache-control"] == "no-store"
        assert phone.post(url + "/api/watchlist", json={"symbol": "AAPL"}).status_code == 200
        assert "AAPL" in desktop.get(upstream + "/api/watchlist").text
        assert (
            phone.post(
                url + "/api/watchlist", json={"symbol": "MSFT"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert phone.get(url + "/api/portfolio", headers={"Host": "evil.example"}).status_code == 400
        # HTTPS 代理覆盖客户端伪造的降级头；不能变成不安全 Cookie。
        forged = phone.post(url + "/api/auth/login", json={"password": PASSWORD}, headers={"X-Forwarded-Proto": "http"})
        assert "Secure" in forged.headers["set-cookie"]
        logout = phone.post(url + "/api/auth/logout")
        assert logout.status_code == 200
        assert logout.headers["cache-control"] == "no-store"
        assert phone.get(url + "/api/portfolio").status_code == 401
        assert desktop.get(upstream + "/api/portfolio").status_code == 200


def test_untrusted_socket_peer_cannot_spoof_https(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ALLOWED_HOSTS", "phone.private.example")
    # 同一信任配置只接受 IPv4 127.0.0.1；IPv6 回环作为真实的不可信 TCP 来源。
    with server(create_app(engine, PASSWORD, run_worker=False), "::1") as url, client() as remote:
        result = remote.post(
            url + "/api/auth/login",
            json={"password": PASSWORD},
            headers={
                "Host": "phone.private.example",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
        assert result.status_code == 403
        assert result.json()["detail"]["code"] == "https_required"
        assert "set-cookie" not in result.headers


def test_socket_worker_fills_without_browser(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    order = engine.place(request(), NOW)
    engine.provider = ControlledProvider(quote_time=NOW + timedelta(minutes=5), price=Decimal(99))
    restarted = Engine(Store(engine.store.path), engine.provider, engine.catalog)
    event = Event()
    original = Worker.tick

    def observed(worker: Worker) -> None:
        original(worker)
        event.set()

    monkeypatch.setattr(worker_module, "datetime", MarketClock)
    monkeypatch.setattr(Worker, "tick", observed)
    with server(create_app(restarted, PASSWORD)):
        assert event.wait(timeout=5)
        persisted = Store(engine.store.path).read()
        assert persisted.orders[0].id == order.id
        assert persisted.orders[0].status == "filled"
        assert len(persisted.ledger) == 2
        assert persisted.lots[0].quantity == 100
