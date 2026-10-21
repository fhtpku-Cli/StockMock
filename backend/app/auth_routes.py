"""启动：uv run uvicorn app.main:app --host 127.0.0.1 --port 8000。"""

from fastapi import FastAPI, Request, Response

from app.api_models import (
    Ok,
)
from app.auth import Auth, Login, SessionView


def register(app: FastAPI, auth: Auth) -> None:
    """注册本模块的个人账户 HTTP 接口。"""

    @app.get("/api/auth/session")
    def session(request: Request) -> SessionView:
        return SessionView(authenticated=auth.valid(request), configured=auth.configured())

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response) -> SessionView:
        """限流并校验密码，生成十二小时会话。"""
        token = auth.login(request, body.password)
        response.set_cookie(
            "paper_session",
            token,
            httponly=True,
            secure=auth.secure,
            samesite="strict",
            max_age=43200,
        )
        return SessionView(authenticated=True, configured=True)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> Ok:
        _ = auth.sessions.pop(request.cookies.get("paper_session", ""), None)
        response.delete_cookie("paper_session")
        return Ok()
