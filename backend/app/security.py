"""显式私有入口白名单，不自行信任客户端转发头。"""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.models import DomainError


@dataclass(frozen=True, slots=True)
class AccessConfig:
    """主机与来源须精确配置，远程入口要求 HTTPS。"""

    hosts: tuple[str, ...]
    origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "AccessConfig":
        """拒绝任意域名通配与不安全远程来源。"""
        hosts = tuple(
            filter(
                None,
                (value.strip() for value in os.getenv("PAPER_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")),
            )
        )
        origins = tuple(
            filter(
                None,
                (
                    value.strip()
                    for value in os.getenv(
                        "PAPER_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
                    ).split(",")
                ),
            )
        )
        if not hosts or any("*" in host or "/" in host or "@" in host for host in hosts):
            raise DomainError("invalid_hosts", "PAPER_ALLOWED_HOSTS 必须列出精确主机名，不允许通配")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
                or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
            ):
                raise DomainError("invalid_origin", "来源必须是精确 HTTPS 地址；本机允许 HTTP")
        return cls(hosts, origins)


def register(app: FastAPI, config: AccessConfig) -> None:
    """服务器负责可信代理解析；应用只用已解析 scheme 与主机白名单。"""
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.hosts), www_redirect=False)

    @app.middleware("http")
    async def origin_guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin")
        local = request.url.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}
        if not local and request.url.scheme != "https":
            return JSONResponse(
                {"detail": {"code": "https_required", "message": "私有远程入口必须使用 HTTPS"}}, status_code=403
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            allowed = {f"{request.url.scheme}://{request.headers.get('host', '')}", *config.origins}
            if origin not in allowed:
                return JSONResponse(
                    {"detail": {"code": "origin_rejected", "message": "拒绝跨站写入请求"}}, status_code=403
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
