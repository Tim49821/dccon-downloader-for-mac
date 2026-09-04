"""로그인 이동과 새 창 링크가 같은 앱 세션을 사용하는지 검증."""

import json

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage

from dccon.url_policy import is_allowed_main_url
from dccon.webview import DcconWebView


@pytest.mark.parametrize("url", [
    "https://sign.dcinside.com/login?s_url=https%3A%2F%2Fdccon.dcinside.com%2F",
    "https://sign.dcinside.com/login/login",
    "https://sign.dcinside.com/logout",
    "https://sso.dcinside.com/auth/?command=attach&broker=dcinside&token=test-only&return_url=https%3A%2F%2Fsign.dcinside.com%2Flogin%2Fmember_check",
    "https://mall.dcinside.com/",
    "https://dccon.dcinside.com/mycon",
])
def test_login_and_return_urls_are_allowed(url):
    assert is_allowed_main_url(url)


@pytest.mark.parametrize("url", [
    "http://sign.dcinside.com/login",
    "https://sign.dcinside.com.evil.example/login",
    "https://evil-dcinside.com/login",
    "https://sign.dcinside.com@evil.example/login",
    "http://sso.dcinside.com/auth/",
    "https://sso.dcinside.com.evil.example/auth/",
    "https://sso.dcinside.com@evil.example/auth/",
    "https://unrelated.dcinside.com/",
    "https://example.com/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,login",
])
def test_login_does_not_allow_untrusted_navigation(url):
    assert not is_allowed_main_url(url)


@pytest.fixture
def webview(qtbot):
    view = DcconWebView()
    qtbot.addWidget(view)
    # The exporter DOM observer is unrelated to navigation and schedules timers.
    view.page().loadFinished.disconnect(view._on_load_finished)
    with qtbot.waitSignal(view.loadFinished, timeout=5000):
        view.setUrl(QUrl("about:blank"))
    yield view
    # Destroy the page before its off-the-record profile.
    with qtbot.waitSignal(view.page().destroyed, timeout=1000):
        view.page().deleteLater()


@pytest.mark.parametrize("url", [
    "https://sign.dcinside.com/login",
    "https://sso.dcinside.com/auth/?command=attach&broker=dcinside&token=test-only&return_url=https%3A%2F%2Fsign.dcinside.com%2Flogin%2Fmember_check",
    "https://sign.dcinside.com/login/member_check",
    "https://mall.dcinside.com/",
])
def test_login_link_and_form_submission_are_not_blocked(webview, url):
    blocked = []
    webview.navigationBlocked.connect(blocked.append)
    for nav_type in (
        QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
        QWebEnginePage.NavigationType.NavigationTypeFormSubmitted,
        QWebEnginePage.NavigationType.NavigationTypeRedirect,
    ):
        assert webview.page().acceptNavigationRequest(
            QUrl(url), nav_type, True
        )
    assert blocked == []


@pytest.mark.parametrize("url", [
    "https://sign.dcinside.com/login?s_url=https%3A%2F%2Fdccon.dcinside.com%2F",
    "https://mall.dcinside.com/",
])
def test_new_window_link_navigates_existing_page(webview, qtbot, url):
    page = webview.page()
    profile = page.profile()
    navigations = []

    def capture_navigation(request):
        navigations.append((request.url().toString(), request.isMainFrame()))
        # Check the real navigation without contacting the public login service.
        request.reject()

    page.navigationRequested.connect(capture_navigation)
    page.runJavaScript(f"window.open({json.dumps(url)}, '_blank');")
    qtbot.waitUntil(lambda: (url, True) in navigations, timeout=3000)
    assert webview.page() is page
    assert page.profile() is profile
    assert profile.isOffTheRecord()


def test_external_popup_is_blocked_and_reported(webview, qtbot):
    url = "https://example.com/"
    with qtbot.waitSignal(webview.navigationBlocked, timeout=3000) as blocked:
        webview.page().runJavaScript(f"window.open({json.dumps(url)}, '_blank');")
    assert blocked.args == [url]
    assert webview.url() == QUrl("about:blank")
