"""Run the actual injected JS, WebChannel, and save UI without site credentials."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

from dccon.main_window import MainWindow
from dccon.webview import DcconWebPage, DcconWebView


PAGE_URL = "https://dccon.dcinside.com/mycon"
LIST_HTML = '<div class="dccon_list_box"><h3>내 디시콘</h3><ul>' + ''.join(
    f'<li><img src="https://dcimg5.dcinside.com/dccon.php?no=thumb{i}"></li>'
    for i in range(8)
) + '</ul></div>'


def detail_html(package_id="101", title="첫 번째 묶음"):
    # Minimal structure from the public #dccon_detail-tmpl, plus nested/hidden lists.
    return f'''<div id="detail_parent"><div id="package_detail" class="pop_wrap"
        data-package-idx="{package_id}">
      <div class="pop_content dccon_popinfo"><div class="pop_head"><h3>디시콘 정보</h3></div>
        <div class="info_viewimg"><img src="https://dcimg5.dcinside.com/dccon.php?no=cover"></div>
        <div class="viewtxt_top"><h4 class="font_blue">{title}</h4></div>
        <div class="dccon_list_wrap"><div class="dccon_list_box">
          <ul class="dccon_list"><li><img src="https://dcimg5.dcinside.com/dccon.php?no=one" alt="웃음"></li>
            <li><ul><li><img src="https://dcimg5.dcinside.com/dccon.php?no=two" alt=""></li></ul></li>
            <li><img src="https://dcimg5.dcinside.com/dccon.php?no=one" alt="또 웃음"></li>
            <li style="display:none"><img src="https://dcimg5.dcinside.com/dccon.php?no=hidden"></li>
            <li><img src="https://evil.example/dccon.php?no=evil"></li>
          </ul>
        </div></div>
      </div></div></div>'''


class BlockNetwork(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme() in ("http", "https"):
            info.block(True)


@pytest.fixture
def window(qtbot, monkeypatch):
    # Only bypass external startup navigation, first-run dialog, and data-URL fixture loading.
    monkeypatch.setattr(DcconWebView, "navigate_home", lambda self: None)
    monkeypatch.setattr(MainWindow, "_show_first_run_notice_if_needed", lambda self: None)
    original_accept = DcconWebPage.acceptNavigationRequest
    monkeypatch.setattr(DcconWebPage, "acceptNavigationRequest", lambda self, url, kind, main:
        True if url.scheme() == "data" else original_accept(self, url, kind, main))
    widget = MainWindow()
    qtbot.addWidget(widget)
    blocker = BlockNetwork(widget)
    widget.webview.page().profile().setUrlRequestInterceptor(blocker)
    widget.show()
    yield widget
    with qtbot.waitSignal(widget.webview.page().destroyed, timeout=2000):
        widget.webview.page().deleteLater()


def load_page(window, qtbot, body, url=PAGE_URL):
    with qtbot.waitSignal(window.webview.loadFinished, timeout=5000):
        window.webview.setHtml(
            '<!doctype html><html><head><style>img {width:32px;height:32px}</style></head><body>'
            + body + '</body></html>', QUrl(url))


def evaluate(window, qtbot, script):
    result = []
    window.webview.page().runJavaScript(script, result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=3000)
    return result[0]


def extract(window, qtbot):
    results = []
    window.webview.run_extract(results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=3000)
    return json.loads(results[0])


def wait_for_detection(window, qtbot, package_id):
    qtbot.waitUntil(lambda: bool(window.detected_info) and
        window.detected_info.get("packageId") == package_id, timeout=4000)


def test_list_alone_is_not_a_package(window, qtbot):
    load_page(window, qtbot, LIST_HTML)
    result = extract(window, qtbot)
    assert result["items"] == []
    assert result["error"]
    qtbot.waitUntil(lambda: window.detected_info is not None, timeout=4000)
    assert not window.detected_info["hasLayer"]
    assert not window.btn_save.isEnabled()


def test_extract_only_active_list_once_in_dom_order(window, qtbot):
    load_page(window, qtbot, LIST_HTML + detail_html())
    result = extract(window, qtbot)
    assert result["package_id"] == "101"
    assert result["title"] == "첫 번째 묶음"
    assert [item["label"] for item in result["items"]] == ["웃음", "", "또 웃음"]
    assert [item["order"] for item in result["items"]] == [1, 2, 3]
    assert result["items"][0]["url"] == result["items"][2]["url"]


def test_ajax_replace_updates_ui_without_url_change(window, qtbot):
    load_page(window, qtbot, LIST_HTML)
    evaluate(window, qtbot, f"document.body.insertAdjacentHTML('beforeend', {json.dumps(detail_html())});")
    wait_for_detection(window, qtbot, "101")
    assert window.btn_save.isEnabled()
    assert window.detected_info["count"] == 3
    evaluate(window, qtbot,
        f"document.querySelector('#detail_parent').outerHTML = {json.dumps(detail_html('202', '두 번째 묶음'))};")
    wait_for_detection(window, qtbot, "202")
    assert "두 번째 묶음" in window.status_label.text()
    assert window.webview.url().toString() == PAGE_URL
    assert extract(window, qtbot)["package_id"] == "202"


@pytest.mark.parametrize("selector", ["#package_detail", "#detail_parent"])
def test_closing_or_hiding_detail_disables_save(window, qtbot, selector):
    load_page(window, qtbot, LIST_HTML + detail_html())
    evaluate(window, qtbot, f"document.querySelector({json.dumps(selector)}).style.display = 'none';")
    assert extract(window, qtbot)["items"] == []
    qtbot.waitUntil(lambda: window.detected_info is not None and
        not window.detected_info["hasLayer"], timeout=4000)
    assert not window.btn_save.isEnabled()


def test_title_and_package_attribute_mutations_update_ui(window, qtbot):
    load_page(window, qtbot, detail_html())
    wait_for_detection(window, qtbot, "101")
    evaluate(window, qtbot, '''
        document.querySelector('#package_detail').setAttribute('data-package-idx', '303');
        document.querySelector('h4').firstChild.data = '바뀐 제목';
    ''')
    wait_for_detection(window, qtbot, "303")
    assert "바뀐 제목" in window.status_label.text()


def test_jquery_data_is_read_from_active_layer_not_url(window, qtbot):
    load_page(window, qtbot, detail_html(), PAGE_URL + "#old")
    # Model only the external site's jQuery .data('data') contract; the reader is real.
    evaluate(window, qtbot, '''
        const active = document.querySelector('#package_detail');
        active.removeAttribute('data-package-idx');
        window.jQuery = (element) => ({
          data: (key) => element === active && key === 'data' ? {package_idx: 404} : undefined
        });
    ''')
    result = extract(window, qtbot)
    assert result["package_id"] == "404"


def test_save_reextracts_latest_package_before_file_dialog(window, qtbot, monkeypatch):
    from dccon.main_window import QFileDialog
    load_page(window, qtbot, detail_html())
    # Stop at the save dialog boundary: no download or user files are written.
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    evaluate(window, qtbot,
        f"document.querySelector('#detail_parent').outerHTML = {json.dumps(detail_html('505', '저장할 묶음'))};")
    window._on_save_clicked()
    qtbot.waitUntil(lambda: window.pending_package is not None, timeout=3000)
    assert window.pending_package.package_id == "505"
    assert window.pending_package.title == "저장할 묶음"
    assert len(window.pending_package.items) == 3


def test_empty_detail_does_not_enable_save(window, qtbot):
    load_page(window, qtbot, '<div id="package_detail" data-package-id="606"><h4>로딩 중</h4></div>')
    wait_for_detection(window, qtbot, "606")
    assert not window.btn_save.isEnabled()


def test_original_fixture_remains_supported(window, qtbot):
    html = (Path(__file__).parent / "fixtures" / "minimal_detail.html").read_text()
    load_page(window, qtbot, html)
    result = extract(window, qtbot)
    assert result["package_id"] == "999"
    assert result["title"] == "테스트 묶음"
    assert [item["label"] for item in result["items"]] == ["웃음", "", "눈물", "웃음2"]
    assert [item["order"] for item in result["items"]] == [1, 2, 3, 4]


def test_navigation_clears_old_selection_and_skips_auth_pages(window, qtbot):
    load_page(window, qtbot, detail_html())
    wait_for_detection(window, qtbot, "101")
    load_page(window, qtbot, "<h1>로그인</h1>", "https://sign.dcinside.com/login")
    assert not window.btn_save.isEnabled()
    assert not window.detected_info["hasLayer"]
    assert evaluate(window, qtbot, "typeof window.__dcconReadDetail") == "undefined"


def test_blocked_navigation_keeps_visible_package_saveable(window, qtbot):
    load_page(window, qtbot, detail_html())
    wait_for_detection(window, qtbot, "101")
    with qtbot.waitSignal(window.webview.loadFinished, timeout=3000):
        evaluate(window, qtbot, "location.href = 'https://example.com/';")
    assert window.webview.url().toString() == PAGE_URL
    assert extract(window, qtbot)["package_id"] == "101"
    qtbot.waitUntil(window.btn_save.isEnabled, timeout=3000)


def test_mall_frame_opens_dccon_as_main_page_with_same_session(window, qtbot):
    # mall.dcinside.com is a wrapper, not the document containing the detail DOM.
    load_page(window, qtbot, '<iframe id="mainContents" name="mainContents"></iframe>',
              "https://mall.dcinside.com/")
    page = window.webview.page()
    profile = page.profile()
    target = "https://dccon.dcinside.com/my/buy_list?page=2"
    navigations = []

    def capture(request):
        navigations.append((request.url().toString(), request.isMainFrame()))
        if request.isMainFrame():
            request.reject()  # No network; test the real Qt navigation boundary.

    page.navigationRequested.connect(capture)
    evaluate(window, qtbot, f"document.querySelector('#mainContents').src = {json.dumps(target)};")
    qtbot.waitUntil(lambda: (target, True) in navigations, timeout=3000)
    assert window.webview.page() is page
    assert page.profile() is profile


@pytest.mark.parametrize("target", [
    "https://example.com/", "https://dccon.dcinside.com.evil.example/",
    "https://sign.dcinside.com/login", "http://dccon.dcinside.com/",
])
def test_mall_does_not_promote_untrusted_or_auth_frames(window, qtbot, target):
    load_page(window, qtbot, '<iframe id="mainContents" name="mainContents"></iframe>',
              "https://mall.dcinside.com/")
    from PySide6.QtWebEngineCore import QWebEnginePage
    window.webview.page().acceptNavigationRequest(QUrl(target),
        QWebEnginePage.NavigationType.NavigationTypeOther, False)
    # Drain queued signals; none of these frames may replace the main page.
    qtbot.wait(50)
    assert window.webview.url().toString() == "https://mall.dcinside.com/"


def test_mall_form_submission_is_not_replayed_as_get(window, qtbot):
    from PySide6.QtWebEngineCore import QWebEnginePage
    load_page(window, qtbot, '<iframe name="mainContents"></iframe>', "https://mall.dcinside.com/")
    page = window.webview.page()
    promoted = []
    page.contentNavigationRequested.connect(promoted.append)
    assert page.acceptNavigationRequest(QUrl("https://dccon.dcinside.com/my/buy_list"),
        QWebEnginePage.NavigationType.NavigationTypeFormSubmitted, False)
    assert promoted == []
