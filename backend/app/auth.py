"""个人密码登录；会话仅保存在内存，重启后重新登录。"""

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Final, final

from fastapi import Request

from app.models import DomainError, Model

MIN_PASSWORD_LENGTH: Final = 12
MAX_LOGIN_ATTEMPTS: Final = 10


class Login(Model):
    """个人密码登录输入。"""

    password: str


class SessionView(Model):
    """登录状态，不返回任何凭证。"""

    authenticated: bool
    configured: bool


@final
class Auth:
    """会话登记表随登录、退出和到期而变更。"""

    def __init__(self, password: str, *, secure: bool = False) -> None:
        """保存依赖并初始化本实例的状态。"""
        self.password = password
        self.secure = secure
        self.sessions: dict[str, datetime] = {}
        self.failures: dict[str, list[datetime]] = {}

    def configured(self) -> bool:
        """确认服务端已配置足够长度的密码。"""
        return len(self.password) >= MIN_PASSWORD_LENGTH

    def valid(self, request: Request) -> bool:
        """核验会话令牌及其到期时间。"""
        token = request.cookies.get("paper_session", "")
        expiry = self.sessions.get(token)
        return expiry is not None and expiry > datetime.now(UTC)

    def require(self, request: Request) -> None:
        """阻止未登录访问个人数据。"""
        if not self.valid(request):
            raise DomainError("login_required", "请先登录个人账户", 401)

    def login(self, request: Request, password: str) -> str:
        """限流并校验密码，生成十二小时会话。"""
        if not self.configured():
            raise DomainError(
                "password_not_configured",
                "请在服务端配置至少 12 字符的 PAPER_PASSWORD",
                503,
            )
        now = datetime.now(UTC)
        host = request.client.host if request.client else "unknown"
        attempts = [at for at in self.failures.get(host, []) if at > now - timedelta(minutes=5)]
        self.failures[host] = attempts
        if len(attempts) >= MAX_LOGIN_ATTEMPTS:
            raise DomainError("login_rate_limited", "登录尝试过多，请五分钟后重试", 429)
        if not hmac.compare_digest(password.encode(), self.password.encode()):
            attempts.append(now)
            raise DomainError("invalid_password", "密码不正确", 401)
        _ = self.failures.pop(host, None)
        self.sessions = {token: expiry for token, expiry in self.sessions.items() if expiry > now}
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + timedelta(hours=12)
        return token
