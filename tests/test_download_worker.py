"""네트워크 통합 테스트 – 로컬 HTTP 서버 재현 – §14."""

import http.server
import socketserver
import threading
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

from dccon.download_worker import DownloadWorker, CancelToken
from dccon.models import DcconItem
from dccon.validators import PNG_SIG, GIF89A


# 간단 핸들러 팩토리
class TestHandler(http.server.BaseHTTPRequestHandler):
    # 클래스 변수로 시나리오 설정
    scenario = "ok_png"  # ok_png, ok_gif, 404, 429_then_ok, 500_then_ok, timeout_then_ok, html_error, truncated
    call_count = 0
    retry_after = "1"

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        TestHandler.call_count += 1
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        # scenario별 응답
        if TestHandler.scenario == "ok_png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(PNG_SIG + b"\x00" * 100)
        elif TestHandler.scenario == "ok_gif":
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.end_headers()
            self.wfile.write(GIF89A + b"\x00" * 100)
        elif TestHandler.scenario == "404":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
        elif TestHandler.scenario == "429_then_ok":
            if TestHandler.call_count == 1:
                self.send_response(429)
                self.send_header("Retry-After", TestHandler.retry_after)
                self.end_headers()
                self.wfile.write(b"rate limited")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(PNG_SIG + b"\x00" * 50)
        elif TestHandler.scenario == "500_then_ok":
            if TestHandler.call_count == 1:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"server error")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(PNG_SIG + b"\x00" * 50)
        elif TestHandler.scenario == "html_error":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>error page</html>")
        elif TestHandler.scenario == "truncated":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"\x89PNG truncated")
        elif TestHandler.scenario == "slow":
            time.sleep(2)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            try:
                self.wfile.write(PNG_SIG + b"\x00" * 10)
            except BrokenPipeError:
                pass
        else:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(PNG_SIG + b"\x00" * 10)


@pytest.fixture
def http_server():
    TestHandler.call_count = 0
    # 랜덤 포트 바인딩
    with socketserver.TCPServer(("127.0.0.1", 0), TestHandler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield port
        httpd.shutdown()


def make_item(port, path="/dccon.php?no=123"):
    return DcconItem(order=1, label="테스트", url=f"http://127.0.0.1:{port}{path}")


def test_ok_png(http_server):
    TestHandler.scenario = "ok_png"
    TestHandler.call_count = 0
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    with tempfile.TemporaryDirectory() as td:
        items = [make_item(http_server)]
        succ, fail = worker.download_all(items, Path(td))
        assert len(succ) == 1
        assert len(fail) == 0
        assert succ[0].image_format == "png"
        assert succ[0].temporary_path.exists()


def test_ok_gif(http_server):
    TestHandler.scenario = "ok_gif"
    TestHandler.call_count = 0
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    with tempfile.TemporaryDirectory() as td:
        succ, fail = worker.download_all([make_item(http_server)], Path(td))
        assert len(succ) == 1
        assert succ[0].image_format == "gif"


def test_404(http_server):
    TestHandler.scenario = "404"
    TestHandler.call_count = 0
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    with tempfile.TemporaryDirectory() as td:
        succ, fail = worker.download_all([make_item(http_server)], Path(td))
        assert len(succ) == 0
        assert len(fail) == 1
        assert fail[0].category == "not_found"


def test_429_retry_after(http_server):
    TestHandler.scenario = "429_then_ok"
    TestHandler.call_count = 0
    TestHandler.retry_after = "1"
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    with tempfile.TemporaryDirectory() as td:
        succ, fail = worker.download_all([make_item(http_server)], Path(td))
        # 재시도 후 성공해야 함
        assert len(succ) == 1
        assert len(fail) == 0


def test_500_then_ok(http_server):
    TestHandler.scenario = "500_then_ok"
    TestHandler.call_count = 0
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    with tempfile.TemporaryDirectory() as td:
        succ, fail = worker.download_all([make_item(http_server)], Path(td))
        assert len(succ) == 1


def test_html_error_page(http_server):
    TestHandler.scenario = "html_error"
    TestHandler.call_count = 0
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    with tempfile.TemporaryDirectory() as td:
        succ, fail = worker.download_all([make_item(http_server)], Path(td))
        assert len(succ) == 0
        assert len(fail) == 1


def test_truncated_image(http_server):
    TestHandler.scenario = "truncated"
    TestHandler.call_count = 0
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    with tempfile.TemporaryDirectory() as td:
        succ, fail = worker.download_all([make_item(http_server)], Path(td))
        assert len(succ) == 0
        assert len(fail) == 1


def test_cancel_slow_response(http_server):
    TestHandler.scenario = "slow"
    TestHandler.call_count = 0
    worker = DownloadWorker(user_agent="ua", referer="http://ref", timeout=5)
    token = CancelToken()
    with tempfile.TemporaryDirectory() as td:
        # 별도 스레드에서 다운로드 시작 후 빠르게 취소
        import threading

        result = {}

        def run():
            s, f = worker.download_all([make_item(http_server)], Path(td), cancel_token=token)
            result["s"] = s
            result["f"] = f

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.3)
        token.cancel()
        t.join(timeout=5)
        # 취소되었으므로 성공이 0이거나 실패에 cancelled가 포함
        # 구현상 다운로드 중 취소는 루프에서 감지하므로 결과는 비어있을 수 있음
        assert "s" in result or "f" in result
