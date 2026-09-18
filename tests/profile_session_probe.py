"""별도 프로세스에서 실제 DcconWebView의 세션 쿠키를 기록하거나 확인한다."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer, QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication

from dccon.webview import DcconWebView


MODE, APP_DATA_ROOT = sys.argv[1], Path(sys.argv[2])
COOKIE_NAME = b"dccon_session_probe"
COOKIE_VALUE = b"survived"

app = QApplication([])
app.setApplicationName("디시콘 저장기 테스트")
app.setOrganizationName("dccon")
QStandardPaths.writableLocation = lambda _location: str(APP_DATA_ROOT)

view = DcconWebView()
app_page = view.page()
profile = app_page.profile()
page = QWebEnginePage(profile, view)
store = profile.cookieStore()


def finish(code: int) -> None:
    def delete_app_page() -> None:
        app_page.deleteLater()

    def delete_view() -> None:
        view.deleteLater()

    def quit_app() -> None:
        app.exit(code)

    page.destroyed.connect(delete_app_page)
    app_page.destroyed.connect(delete_view)
    view.destroyed.connect(quit_app)
    page.deleteLater()


def check_document_cookie(found) -> None:
    finish(0 if bool(found) else 1)


def on_cookie_added(cookie) -> None:
    if (
        MODE == "write"
        and bytes(cookie.name()) == COOKIE_NAME
        and bytes(cookie.value()) == COOKIE_VALUE
    ):
        finish(0)


def on_loaded(_ok: bool) -> None:
    if MODE == "write":
        cookie = QNetworkCookie(COOKIE_NAME, COOKIE_VALUE)
        cookie.setDomain(".dcinside.com")
        cookie.setPath("/")
        store.setCookie(cookie, QUrl("https://dccon.dcinside.com/"))
        return

    page.runJavaScript(
        "document.cookie.includes('dccon_session_probe=survived')",
        check_document_cookie,
    )


store.cookieAdded.connect(on_cookie_added)
page.loadFinished.connect(on_loaded)
page.setHtml("<html><body>session probe</body></html>", QUrl("https://dccon.dcinside.com/"))
QTimer.singleShot(10_000, lambda: app.exit(2))
raise SystemExit(app.exec())
