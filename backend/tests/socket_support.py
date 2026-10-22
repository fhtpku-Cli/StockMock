"""真实回环 HTTP/TLS 测试设施；不连接外部服务。"""

import socket
import ssl
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import final, override

import httpx2
import uvicorn
from fastapi import FastAPI
from pydantic import TypeAdapter


def client(context: ssl.SSLContext | None = None) -> httpx2.Client:
    transport = httpx2.HTTPTransport(
        verify=context if context is not None else True,
        trust_env=False,
        http2=True,
        retries=0,
        limits=httpx2.Limits(max_connections=20, max_keepalive_connections=5, keepalive_expiry=5),
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(transport=transport, trust_env=False, timeout=5, follow_redirects=False)


@contextmanager
def server(app: FastAPI, host: str = "127.0.0.1") -> Generator[str]:
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family) as listener:
        listener.bind((host, 0))
        port = TypeAdapter(tuple[str, int] | tuple[str, int, int, int]).validate_python(listener.getsockname())[1]
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            access_log=False,
            log_level="error",
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
        running = uvicorn.Server(config)
        thread = Thread(target=running.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not running.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert running.started, "隔离 Uvicorn 未启动"
            yield f"http://[{host}]:{port}" if family == socket.AF_INET6 else f"http://{host}:{port}"
        finally:
            running.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


@contextmanager
def tls_proxy(upstream: str, directory: Path) -> Generator[tuple[str, ssl.SSLContext]]:
    cert, key = directory / "cert.pem", directory / "key.pem"
    _ = subprocess.run(  # noqa: S603 — 固定 openssl 参数，仅写入 pytest 临时目录。
        [
            "/usr/bin/openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    with client() as backend:

        @final
        class Proxy(BaseHTTPRequestHandler):
            def forward(self) -> None:
                headers = dict(self.headers.items())
                headers["X-Forwarded-Proto"] = "https"
                headers["X-Forwarded-For"] = "192.0.2.20"
                _ = headers.pop("Connection", None)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                result = backend.request(self.command, upstream + self.path, headers=headers, content=body)
                self.send_response(result.status_code)
                for name, value in result.headers.multi_items():
                    if name.lower() not in {"transfer-encoding", "connection", "content-encoding"}:
                        self.send_header(name, value)
                self.end_headers()
                _ = self.wfile.write(result.content)

            do_GET = forward  # noqa: N815 — HTTPServer 协议名称。
            do_POST = forward  # noqa: N815 — HTTPServer 协议名称。

            @override
            def log_message(self, format: str, *args: str) -> None:
                """不记录 Cookie、路径或测试密码。"""

        with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as proxy:
            proxy.socket = tls.wrap_socket(proxy.socket, server_side=True)
            thread = Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"https://localhost:{proxy.server_port}", ssl.create_default_context(cafile=str(cert))
            finally:
                proxy.shutdown()
                thread.join(timeout=5)
