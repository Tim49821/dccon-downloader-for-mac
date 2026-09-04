"""DcconWebView – QWebEngineView + 탐색 정책 + 쿠키/UA 접근 – §6, §12."""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal, QUrl, QFile, QIODevice, Qt
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget
from PySide6.QtWebChannel import QWebChannel

from .page_bridge import PageBridge
from .url_policy import is_allowed_main_url

logger = logging.getLogger(__name__)


class DcconWebPage(QWebEnginePage):
    navigationBlocked = Signal(str)
    contentNavigationRequested = Signal(QUrl)

    def __init__(self, profile: QWebEngineProfile, parent=None) -> None:
        super().__init__(profile, parent)
        self.newWindowRequested.connect(self._on_new_window_requested)

    def acceptNavigationRequest(self, url: QUrl, _type, isMainFrame: bool) -> bool:
        # file://, javascript:, data: 차단 §12
        scheme = url.scheme().lower()
        if scheme in ("file", "javascript", "data"):
            self.navigationBlocked.emit(url.toString())
            return False
        # mall is an iframe wrapper. Promote only its HTTPS dccon content to
        # this same page/profile, so detection, extraction and history use the
        # actual document. Never replay form submissions as GET requests.
        if (not isMainFrame and self.url().scheme() == "https"
                and self.url().host() == "mall.dcinside.com"
                and scheme == "https" and url.host() == "dccon.dcinside.com"
                and _type != QWebEnginePage.NavigationType.NavigationTypeFormSubmitted):
            self.contentNavigationRequested.emit(url)
            return False
        if isMainFrame:
            url_str = url.toString()
            # about:blank 등 초기 로드 허용
            if url_str in ("about:blank",):
                return True
            # 허용된 메인 호스트만 허용
            # 단, dccon 페이지 내부의 리다이렉트/검색 파라미터 등은 같은 호스트이므로 is_allowed_main_url로 검사
            # 외부 도메인으로의 이동은 차단
            if not is_allowed_main_url(url_str):
                # 외부 탐색 차단 – 상태 영역에 안내는 MainWindow에서 처리
                # 하지만 dcinside.com 하위 이미지 CDN 등은 메인 프레임이 아니므로 여기서는 메인 프레임만 차단
                # 메인 프레임이 허용되지 않은 호스트면 차단
                from urllib.parse import urlparse

                parsed = urlparse(url_str)
                host = (parsed.hostname or "").lower()
                # 빈 host (예: about:)는 이미 위에서 처리
                # 허용되지 않은 host면 차단
                if host:
                    self.navigationBlocked.emit(url_str)
                    return False
        return super().acceptNavigationRequest(url, _type, isMainFrame)

    def _on_new_window_requested(self, request) -> None:
        url = request.requestedUrl().toString()
        if not is_allowed_main_url(url):
            self.navigationBlocked.emit(url)
            return
        # 새 창 링크도 현재 페이지에서 열어 로그인 쿠키와 브라우저 UI를 유지한다.
        # URL만 다시 로드하지 않고 원래 요청(POST 포함)을 전달한다.
        request.openIn(self)


class DcconWebView(QWebEngineView):
    navigationBlocked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 오프더레코드 프로필 – §12 세션 (디스크에 남기지 않음)
        # 이름 없는 생성자가 OffTheRecord=True (Qt6)
        self._profile = QWebEngineProfile(self)
        # 캐시/방문기록도 휘발성으로 유지 (Qt 기본이 off-the-record면 자동)
        self._page = DcconWebPage(self._profile, self)
        self._page.navigationBlocked.connect(self.navigationBlocked)
        # Wait until the subframe navigation callback returns before loading.
        self._page.contentNavigationRequested.connect(self.setUrl, Qt.ConnectionType.QueuedConnection)
        self.setPage(self._page)

        # 새 창 요청은 DcconWebPage에서 허용 URL만 같은 화면으로 연결한다.
        settings = self._page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)

        # QWebChannel 브리지
        self.bridge = PageBridge(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self._page.setWebChannel(self.channel)

        # Embed the bundled WebChannel runtime before the observer. A remote page
        # must not race a dynamically loaded qrc script or an arbitrary timer.
        runtime = QFile(":/qtwebchannel/qwebchannel.js")
        if not runtime.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("Qt WebChannel 스크립트를 읽을 수 없습니다.")
        channel_js = bytes(runtime.readAll()).decode("utf-8")
        runtime.close()
        script = QWebEngineScript()
        script.setName("dccon-detail-observer")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        # The site's jQuery .data() cache lives in MainWorld.
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(
            "(function(){ if (location.protocol !== 'https:' || "
            "!['dccon.dcinside.com', 'mall.dcinside.com'].includes(location.hostname)) return;\n"
            + channel_js + "\n" + PageBridge.load_observer_js() + "\n})();"
        )
        self._page.scripts().insert(script)
        self._page.loadStarted.connect(self._clear_detail)
        self._page.loadFinished.connect(self._on_load_finished)

    def _clear_detail(self) -> None:
        self.bridge.detailChanged.emit({"hasLayer": False, "packageId": "", "title": "", "count": 0})

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self._clear_detail()
            # Rejected navigation can leave the original document alive. Ask its
            # observer to restore the visible selection, even if it is unchanged.
            self._page.runJavaScript("window.dispatchEvent(new Event('dccon-refresh-detail'));")

    def get_user_agent(self, callback) -> None:
        """현재 프로필의 User-Agent를 비동기로 가져옴."""
        # QWebEngineProfile.httpUserAgent()는 동기
        try:
            ua = self._profile.httpUserAgent()
            callback(ua)
        except Exception:
            callback("Mozilla/5.0")

    def get_cookies_string(self, callback) -> None:
        """dcinside 관련 쿠키 문자열을 비동기로 가져옴 – §9."""
        try:
            store = self._profile.cookieStore()
            collected: list[str] = []

            def on_cookie_added(cookie):
                try:
                    # QNetworkCookie: name()/value() -> QByteArray
                    try:
                        name = bytes(cookie.name()).decode("utf-8", errors="ignore")
                        value = bytes(cookie.value()).decode("utf-8", errors="ignore")
                    except Exception:
                        # fallback: str 변환
                        name = str(cookie.name())
                        value = str(cookie.value())
                    if name:
                        # dcinside 관련 쿠키만 포함 (보수적이지만 전체도 허용)
                        # spec상 앱 세션 쿠키만 사용하므로 전체 수집
                        collected.append(f"{name}={value}")
                except Exception:
                    pass

            store.cookieAdded.connect(on_cookie_added)
            store.loadAllCookies()

            from PySide6.QtCore import QTimer

            def done():
                try:
                    store.cookieAdded.disconnect(on_cookie_added)
                except Exception:
                    pass
                # 중복 제거 유지
                seen = set()
                uniq = []
                for kv in collected:
                    if kv not in seen:
                        seen.add(kv)
                        uniq.append(kv)
                logger.debug("쿠키 %d개 수집", len(uniq))
                callback("; ".join(uniq))

            # loadAllCookies는 cookieAdded 시그널을 비동기로 발생시키므로 짧게 대기
            QTimer.singleShot(500, done)
        except Exception as e:
            logger.warning("쿠키 수집 실패: %s", e)
            callback("")

    def navigate_home(self) -> None:
        self.setUrl(QUrl("https://dccon.dcinside.com/"))

    def run_extract(self, callback) -> None:
        js = PageBridge.load_extract_js()
        self._page.runJavaScript(js, callback)
