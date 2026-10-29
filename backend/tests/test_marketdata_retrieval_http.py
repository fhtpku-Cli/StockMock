"""真实本机HTTP验证缓存刷新；载荷是受控数据，不冒充真实收盘。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import override

from app.marketdata import MarketDataClient
from tests.test_marketdata_retrieval import daily


def test_http_refreshes_unexpired_daily_cache_once() -> None:
    calls: list[str] = []
    payload = [daily("100")]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload[0])))
            self.end_headers()
            _ = self.wfile.write(payload[0])

        @override
        def log_message(self, format: str, *args: str) -> None:
            return

    class Client(MarketDataClient):
        @override
        def _get(self, url: str) -> bytes:
            return super()._get(f"http://127.0.0.1:{server.server_port}/history")

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Client()
            before = client.history("600519.SS")
            boundary = datetime.now(UTC)
            payload[0] = daily("105")
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(client.history, "600519.SS", retrieved_not_before=boundary) for _ in range(8)]
                result = [future.result(timeout=10) for future in futures]
            assert before[0].close == 100
            assert before[0].retrieved_at < boundary
            assert all(bars[0].close == 105 and bars[0].retrieved_at >= boundary for bars in result)
            assert len({bars[0].retrieved_at for bars in result}) == 1
            assert calls == ["/history", "/history"]
            assert client.history("600519.SS") == result[0]
            assert len(calls) == 2
        finally:
            server.shutdown()
            thread.join(timeout=5)
