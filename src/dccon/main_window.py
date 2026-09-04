"""MainWindow – §4, §5, §6."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Slot, QThread, Signal, QObject, QSize, QStandardPaths
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QSizePolicy,
)

from .export_controller import ExportController, ExportState
from .filename_policy import zip_filename
from .models import DcconPackage
from .webview import DcconWebView

logger = logging.getLogger(__name__)

HOME_URL = "https://dccon.dcinside.com/"


class ExportWorker(QObject):
    """QThread에서 다운로드+팩킹을 수행 – UI 블로킹 금지 §4."""

    progress = Signal(object)  # ExportProgress
    finished = Signal(object)  # Path | None
    failed = Signal(str)

    def __init__(
        self,
        controller: ExportController,
        package: DcconPackage,
        destination: Path,
        user_agent: str,
        referer: str,
        cookies: str | None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.package = package
        self.destination = destination
        self.user_agent = user_agent
        self.referer = referer
        self.cookies = cookies

    @Slot()
    def run(self):
        try:
            ok = self.controller.prepare(
                self.package,
                self.destination,
                user_agent=self.user_agent,
                referer=self.referer,
                cookies=self.cookies,
            )
            if not ok:
                self.failed.emit(self.controller.state.name)
                return
            self.controller.set_progress_callback(lambda p: self.progress.emit(p))
            successes, failures = self.controller.start_download(
                user_agent=self.user_agent,
                referer=self.referer,
                cookies=self.cookies,
            )
            if self.controller.cancel_token and self.controller.cancel_token.cancelled:
                self.failed.emit("취소됨")
                return
            if failures:
                self.failed.emit(f"{len(failures)}개 실패")
                return
            result = self.controller.pack_complete_if_all_succeeded()
            if result:
                self.finished.emit(result)
            else:
                self.failed.emit("ZIP 생성 실패")
        except Exception as e:
            logger.exception("ExportWorker error")
            self.failed.emit(str(e))


class RetryWorker(QObject):
    done = Signal(object)
    fail = Signal(str)
    prog = Signal(object)

    def __init__(self, ctrl: ExportController, ua_: str, ref_: str, ck_: str | None):
        super().__init__()
        self.ctrl = ctrl
        self.ua = ua_
        self.ref = ref_
        self.ck = ck_

    @Slot()
    def run(self):
        self.ctrl.set_progress_callback(lambda p: self.prog.emit(p))
        s, f = self.ctrl.retry_failed(self.ua, self.ref, self.ck)
        if f:
            self.fail.emit(f"{len(f)}개 실패")
        else:
            res = self.ctrl.pack_complete_if_all_succeeded()
            if res:
                self.done.emit(res)
            else:
                self.fail.emit("ZIP 생성 실패")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("디시콘 저장기")
        self.resize(1100, 800)
        # macOS 통합 툴바 비활성화 – 흰색 여백 방지
        self.setUnifiedTitleAndToolBarOnMac(False)

        self.current_package: DcconPackage | None = None
        self.pending_package: DcconPackage | None = None
        self.detected_info: dict | None = None
        self.export_controller = ExportController()
        self.export_thread: QThread | None = None
        self.export_worker: ExportWorker | None = None
        self.current_destination: Path | None = None
        self._extract_retry_count: int = 0
        self._is_extracting: bool = False

        # --- 중앙 위젯: 세로 3단 (상단바 / 브라우저 / 상태) ---
        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1) 브라우저 도구 모음 – QToolBar 대신 QWidget으로 구현 (macOS 흰 바 이슈 방지)
        self.top_bar = QFrame(central)
        self.top_bar.setObjectName("topBar")
        self.top_bar.setFrameShape(QFrame.NoFrame)
        self.top_bar.setFixedHeight(42)
        # objectName 선택자로만 배경 적용 – 버튼에 전파 안 되게
        self.top_bar.setStyleSheet(
            "QFrame#topBar { background: #ececec; border-bottom: 1px solid #c8c8c8; }"
        )
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(8, 6, 8, 6)
        top_layout.setSpacing(6)

        def make_nav_btn(text: str) -> QPushButton:
            btn = QPushButton(text, self.top_bar)
            btn.setFixedHeight(28)
            btn.setMinimumWidth(64)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn.setStyleSheet(
                "QPushButton { background: #ffffff; border: 1px solid #b8b8b8; border-radius: 6px; color: #1a1a1a; font-weight: 600; font-size: 12px; }"
                "QPushButton:hover { background: #f2f2f2; border-color: #999; }"
                "QPushButton:pressed { background: #e5e5e5; border-color: #888; }"
            )
            return btn

        self.btn_back = make_nav_btn("뒤로")
        self.btn_back.clicked.connect(self._on_back)
        top_layout.addWidget(self.btn_back)

        self.btn_forward = make_nav_btn("앞으로")
        self.btn_forward.clicked.connect(self._on_forward)
        top_layout.addWidget(self.btn_forward)

        self.btn_home = make_nav_btn("홈")
        self.btn_home.clicked.connect(self._on_home)
        top_layout.addWidget(self.btn_home)

        self.btn_reload = make_nav_btn("새로고침")
        self.btn_reload.clicked.connect(self._on_reload)
        top_layout.addWidget(self.btn_reload)

        # 주소 표시 – QLineEdit readOnly (복사 가능, 편집 불가 느낌)
        self.url_field = QLineEdit(self.top_bar)
        self.url_field.setReadOnly(True)
        self.url_field.setText(HOME_URL)
        self.url_field.setPlaceholderText("주소")
        self.url_field.setFixedHeight(28)
        self.url_field.setStyleSheet(
            "QLineEdit { background: #ffffff; border: 1px solid #bfbfbf; border-radius: 6px; padding: 0 8px; color: #333; selection-background-color: #b3d7ff; }"
        )
        self.url_field.setCursorPosition(0)
        top_layout.addWidget(self.url_field, stretch=1)

        main_layout.addWidget(self.top_bar)

        # 2) 브라우저 영역
        self.webview = DcconWebView(central)
        # QWebEngineView가 확장되도록
        self.webview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.webview, stretch=1)

        # 3) 상태 및 작업 영역 – §5
        self.status_widget = QFrame(central)
        self.status_widget.setObjectName("statusWidget")
        self.status_widget.setFrameShape(QFrame.NoFrame)
        # 높이를 여유 있게 – 버튼이 잘리지 않게
        self.status_widget.setMinimumHeight(96)
        self.status_widget.setMaximumHeight(120)
        # 자식 QPushButton/QLabel에 영향 안 주는 선택자 사용 – 흰 배경으로 CTA 대비 극대화
        self.status_widget.setStyleSheet(
            "QFrame#statusWidget { background: #ffffff; border-top: 1px solid #d0d0d0; }"
        )
        status_layout = QVBoxLayout(self.status_widget)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(8)

        self.status_label = QLabel("저장할 디시콘을 선택하세요.", self.status_widget)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("QLabel#statusLabel { color: #111; background: transparent; border: none; font-weight: 500; }")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        status_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self.status_widget)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #bbb; border-radius: 4px; background: #e9e9e9; }"
            "QProgressBar::chunk { background: #4a90e2; border-radius: 3px; }"
        )
        status_layout.addWidget(self.progress_bar)

        # 버튼 행 – 항상 보이되 상태에 따라 enabled/visible 제어
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)

        self.btn_save = QPushButton("⬇ 현재 디시콘 ZIP 저장", self.status_widget)
        self.btn_save.setObjectName("saveButton")
        self.btn_save.setEnabled(False)
        self.btn_save.setFixedHeight(36)
        self.btn_save.setMinimumWidth(200)
        # 고대비 CTA – 비활성화/활성화 모두 배경과 명확히 구분되도록
        self.btn_save.setStyleSheet(
            "QPushButton#saveButton { background: #0066FF; color: white; border: 2px solid #0052CC; border-radius: 8px; padding: 0 20px; font-weight: 700; font-size: 13px; }"
            "QPushButton#saveButton:disabled { background: #9AA3B2; color: #ffffff; border: 2px solid #9AA3B2; }"
            "QPushButton#saveButton:hover:!disabled { background: #0052CC; border-color: #004099; }"
            "QPushButton#saveButton:pressed:!disabled { background: #004099; border-color: #003080; }"
        )
        self.btn_save.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.btn_save)

        # 보조 버튼 공통 스타일 – 흰 배경+테두리로 가시성 확보 (CTA와 구분되면서도 안 묻힘)
        secondary_btn_style = (
            "QPushButton { background: #ffffff; border: 1.5px solid #b8b8b8; border-radius: 8px; padding: 0 14px; color: #1a1a1a; font-weight: 600; font-size: 12px; }"
            "QPushButton:hover { background: #f2f2f2; border-color: #8a8a8a; }"
            "QPushButton:pressed { background: #e6e6e6; }"
        )

        self.btn_cancel = QPushButton("취소", self.status_widget)
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setStyleSheet(secondary_btn_style)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.btn_cancel)

        self.btn_retry = QPushButton("재시도", self.status_widget)
        self.btn_retry.setFixedHeight(36)
        self.btn_retry.setStyleSheet(
            "QPushButton { background: #FF6B00; color: white; border: 2px solid #E65C00; border-radius: 8px; padding: 0 16px; font-weight: 700; font-size: 12px; }"
            "QPushButton:hover { background: #E65C00; border-color: #CC5200; }"
            "QPushButton:pressed { background: #CC5200; }"
        )
        self.btn_retry.setVisible(False)
        self.btn_retry.clicked.connect(self._on_retry_clicked)
        btn_row.addWidget(self.btn_retry)

        self.btn_save_incomplete = QPushButton("불완전 ZIP 저장", self.status_widget)
        self.btn_save_incomplete.setFixedHeight(36)
        self.btn_save_incomplete.setStyleSheet(secondary_btn_style)
        self.btn_save_incomplete.setVisible(False)
        self.btn_save_incomplete.clicked.connect(self._on_save_incomplete_clicked)
        btn_row.addWidget(self.btn_save_incomplete)

        self.btn_show_in_finder = QPushButton("Finder에서 보기", self.status_widget)
        self.btn_show_in_finder.setFixedHeight(36)
        self.btn_show_in_finder.setStyleSheet(
            "QPushButton { background: #1a1a1a; color: white; border: none; border-radius: 8px; padding: 0 14px; font-weight: 600; font-size: 12px; }"
            "QPushButton:hover { background: #333333; }"
            "QPushButton:pressed { background: #000000; }"
        )
        self.btn_show_in_finder.setVisible(False)
        self.btn_show_in_finder.clicked.connect(self._on_show_in_finder)
        btn_row.addWidget(self.btn_show_in_finder)

        btn_row.addStretch(1)
        status_layout.addLayout(btn_row)

        main_layout.addWidget(self.status_widget)

        # 시그널
        self.webview.urlChanged.connect(self._on_url_changed)
        self.webview.navigationBlocked.connect(self._on_navigation_blocked)
        self.webview.bridge.detailChanged.connect(self._on_detail_changed)
        self.webview.bridge.extractFailed.connect(self._on_extract_failed)
        self.webview.bridge.extractFinished.connect(self._on_extract_finished)

        self.webview.navigate_home()
        self._show_first_run_notice_if_needed()

    # --- 브라우저 툴바 ---
    def _on_back(self):
        self.webview.back()

    def _on_forward(self):
        self.webview.forward()

    def _on_home(self):
        self.webview.navigate_home()

    def _on_reload(self):
        self.webview.reload()

    def _on_url_changed(self, url: QUrl):
        self.url_field.setText(url.toString())
        self.url_field.setCursorPosition(0)

    def _on_navigation_blocked(self, url: str):
        self.status_label.setText(f"외부 이동이 차단되었습니다: {url}")
        logger.info("navigation blocked: %s", url)

    # --- 상세 감지 ---
    @Slot(dict)
    def _on_detail_changed(self, data: dict):
        self.detected_info = data
        has_layer = data.get("hasLayer", False)
        title = data.get("title", "")
        count = data.get("count", 0)
        if self.export_controller.state not in (ExportState.IDLE, ExportState.FAILED, ExportState.COMPLETE):
            return
        if not has_layer:
            self.status_label.setText("저장할 디시콘을 선택하세요.")
            self.btn_save.setEnabled(False)
            self.btn_save.setVisible(True)
            return
        if not title or count <= 0:
            self.status_label.setText("디시콘 정보를 읽는 중…")
            self.btn_save.setEnabled(False)
            self.btn_save.setVisible(True)
            return
        display_title = title or "(제목 없음)"
        self.status_label.setText(f"{display_title} · {count}개 감지됨")
        self.btn_save.setEnabled(True)
        self.btn_save.setVisible(True)
        self._set_idle_ui()

    def _set_idle_ui(self):
        if self.export_controller.state == ExportState.IDLE:
            self.btn_cancel.setVisible(False)
            self.btn_retry.setVisible(False)
            self.btn_save_incomplete.setVisible(False)
            self.btn_show_in_finder.setVisible(False)
            self.progress_bar.setVisible(False)

    # --- 저장 흐름 ---
    @Slot()
    def _on_save_clicked(self):
        if self._is_extracting or self.export_controller.state not in (ExportState.IDLE, ExportState.FAILED, ExportState.COMPLETE):
            QMessageBox.information(self, "알림", "이미 저장 작업이 진행 중입니다.")
            return
        if self.export_controller.state in (ExportState.FAILED, ExportState.COMPLETE):
            self.export_controller.reset()
        self._extract_retry_count = 0
        self._is_extracting = True
        self.status_label.setText("디시콘 정보를 읽는 중…")
        self.btn_save.setEnabled(False)
        self.progress_bar.setVisible(False)
        # 컨트롤러 상태는 prepare()에서 EXTRACTING으로 전이 – 여기서는 UI만 갱신
        self.webview.run_extract(self._on_extract_js_result)

    def _on_extract_js_result(self, result):
        # runJavaScript 경로는 시그널 중복 발사를 피하기 위해 emit_signals=False
        pkg = self.webview.bridge.parse_extract_result(result, emit_signals=False)
        if pkg is None:
            # parse 실패 원인을 직접 분석해 UI에 표시 (이미 _on_extract_failed 로직 재사용)
            try:
                import json
                data = json.loads(result) if isinstance(result, str) else result or {}
                err = (data.get("error") if isinstance(data, dict) else None) or "상세 레이어가 없음"
            except Exception:
                err = "페이지 구조가 변경되어 디시콘을 읽을 수 없습니다."
            self._on_extract_failed(err)
            return
        self.pending_package = pkg
        self._on_extract_finished(pkg)

    @Slot(object)
    def _on_extract_finished(self, pkg: DcconPackage):
        from .validators import ValidationError, validate_package

        # 디버그 로그: 추출 결과 상세
        logger.info("추출 결과: title=%r package_id=%r items=%d source=%s", pkg.title, pkg.package_id, len(pkg.items), pkg.source_url[:80])

        try:
            validate_package(pkg)
        except ValidationError as e:
            msg = str(e)
            logger.warning("검증 실패: %s pkg=%r items=%d", msg, pkg.title, len(pkg.items))
            # 빈 제목/아이템은 렌더 지연일 수 있어 1회 자동 재시도
            if ("패키지명이 비어" in msg or "항목이 0개" in msg) and getattr(self, "_extract_retry_count", 0) < 1:
                self._extract_retry_count = getattr(self, "_extract_retry_count", 0) + 1
                self.status_label.setText("디시콘 정보를 다시 읽는 중…")
                logger.info("검증 실패로 추출 재시도 %d", self._extract_retry_count)
                from PySide6.QtCore import QTimer
                QTimer.singleShot(700, lambda: self.webview.run_extract(self._on_extract_js_result))
                return
            self._extract_retry_count = 0
            self._is_extracting = False
            if "패키지명" in msg or "상세 레이어" in msg:
                self.status_label.setText(f"오류: {msg} (제목 추출 실패 - 디시콘을 다시 클릭해보세요)")
            elif "항목이 0개" in msg:
                self.status_label.setText(f"오류: {msg} (이미지 목록을 찾지 못함 - 상세 레이어가 완전히 로딩된 후 다시 시도)")
            else:
                self.status_label.setText(f"검증 실패: {msg}")
            if "허용되지 않은" in msg:
                self.status_label.setText("페이지 구조가 변경되어 디시콘을 읽을 수 없습니다. 앱 업데이트가 필요합니다.")
            self.btn_save.setEnabled(True)
            self.btn_save.setVisible(True)
            self.export_controller.reset()
            return
        self._extract_retry_count = 0
        self._is_extracting = False

        default_name = zip_filename(pkg.title)
        # 기본 저장 위치: 다운로드 폴더 (§5) – QStandardPaths로 로케일 대응, 없으면 ~/Downloads → HOME 순차 폴백
        downloads_location = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        if downloads_location and Path(downloads_location).is_dir():
            default_dir = Path(downloads_location)
        else:
            downloads_fallback = Path.home() / "Downloads"
            default_dir = downloads_fallback if downloads_fallback.is_dir() else Path.home()
        dest_str, _ = QFileDialog.getSaveFileName(
            self,
            "ZIP 저장 위치 선택",
            str(default_dir / default_name),
            "ZIP Files (*.zip)",
        )
        if not dest_str:
            self.status_label.setText(f"{pkg.title} · {len(pkg.items)}개 감지됨")
            self.btn_save.setEnabled(True)
            self.btn_save.setVisible(True)
            self.export_controller.reset()
            self._is_extracting = False
            return

        dest = Path(dest_str)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")
        self.current_package = pkg
        self.current_destination = dest
        self._start_export(pkg, dest)

    @Slot(str)
    def _on_extract_failed(self, msg: str):
        logger.warning("추출 실패: %s state=%s", msg, self.export_controller.state)
        # 상세 레이어 미노출은 렌더 지연일 수 있어 1회 자동 재시도
        if "상세 레이어가 없음" in msg and getattr(self, "_extract_retry_count", 0) < 1:
            self._extract_retry_count = getattr(self, "_extract_retry_count", 0) + 1
            self.status_label.setText("디시콘 정보를 다시 읽는 중…")
            logger.info("레이어 없음으로 추출 재시도 %d", self._extract_retry_count)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(700, lambda: self.webview.run_extract(self._on_extract_js_result))
            return
        self._extract_retry_count = 0
        self._is_extracting = False
        if "상세 레이어가 없음" in msg:
            self.status_label.setText("저장할 디시콘을 선택하세요. (디시콘을 클릭해 상세 레이어를 연 뒤 다시 시도)")
        elif "페이지 구조" in msg:
            self.status_label.setText("페이지 구조가 변경되어 디시콘을 읽을 수 없습니다. 앱 업데이트가 필요합니다.")
        else:
            self.status_label.setText(f"추출 실패: {msg}")
        self.btn_save.setEnabled(True)
        self.btn_save.setVisible(True)
        self.export_controller.reset()
        # 디버그를 위해 현재 탐지 정보도 로그
        if self.detected_info:
            logger.info("detected_info=%r", self.detected_info)

    def _start_export(self, pkg: DcconPackage, dest: Path):
        def after_ua(ua: str):
            referer = pkg.source_url or HOME_URL

            def after_cookies(cookies: str):
                self._launch_worker(pkg, dest, ua, referer, cookies)

            self.webview.get_cookies_string(after_cookies)

        self.webview.get_user_agent(after_ua)

    def _launch_worker(self, pkg: DcconPackage, dest: Path, ua: str, referer: str, cookies: str):
        self.status_label.setText(f"다운로드 중… 0/{len(pkg.items)}")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(pkg.items))
        self.progress_bar.setValue(0)
        self.btn_save.setEnabled(False)
        self.btn_save.setVisible(True)
        self.btn_cancel.setVisible(True)
        self.btn_retry.setVisible(False)
        self.btn_save_incomplete.setVisible(False)
        self.btn_show_in_finder.setVisible(False)

        self.export_thread = QThread(self)
        self.export_worker = ExportWorker(self.export_controller, pkg, dest, ua, referer, cookies)
        self.export_worker.moveToThread(self.export_thread)
        self.export_thread.started.connect(self.export_worker.run)
        self.export_worker.progress.connect(self._on_export_progress)
        self.export_worker.finished.connect(self._on_export_finished)
        self.export_worker.failed.connect(self._on_export_failed)
        self.export_worker.finished.connect(self.export_thread.quit)
        self.export_worker.failed.connect(self.export_thread.quit)
        self.export_thread.finished.connect(self._cleanup_thread)
        self.export_thread.start()

    @Slot(object)
    def _on_export_progress(self, prog):
        if prog.state == ExportState.DOWNLOADING:
            self.status_label.setText(f"다운로드 중… {prog.completed}/{prog.total}")
            self.progress_bar.setValue(prog.completed)
        elif prog.state == ExportState.PACKING:
            self.status_label.setText("ZIP 생성 중…")

    @Slot(object)
    def _on_export_finished(self, result_path):
        self.progress_bar.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.btn_save.setEnabled(True)
        self.btn_save.setVisible(True)
        self.status_label.setText(f"완료: {result_path}")
        self.btn_show_in_finder.setVisible(True)
        self.current_destination = Path(str(result_path))
        self._on_detail_changed(self.detected_info or {})

    @Slot(str)
    def _on_export_failed(self, msg: str):
        if msg == "취소됨":
            self.status_label.setText("취소됨 – 임시 파일이 정리되었습니다.")
            self.progress_bar.setVisible(False)
            self.btn_cancel.setVisible(False)
            self.btn_save.setEnabled(True)
            self.btn_save.setVisible(True)
            self.export_controller.reset()
            return
        failures = self.export_controller.failures
        total = len(self.current_package.items) if self.current_package else 0
        self.progress_bar.setVisible(False)
        self.btn_cancel.setVisible(False)
        if failures:
            self.status_label.setText(f"실패: {len(failures)}/{total} 개 항목 실패 – 재시도 또는 불완전 ZIP 저장 가능")
            self.btn_retry.setVisible(True)
            self.btn_save_incomplete.setVisible(True)
            for f in failures:
                logger.warning("failure order=%s label=%s cat=%s msg=%s", f.item.order, f.item.label, f.category, f.message)
        else:
            self.status_label.setText(f"오류: {msg}")
        self.btn_save.setEnabled(False)
        self.btn_save.setVisible(True)

    def _cleanup_thread(self):
        if self.export_worker:
            self.export_worker.deleteLater()
            self.export_worker = None
        if self.export_thread:
            self.export_thread.deleteLater()
            self.export_thread = None

    @Slot()
    def _on_cancel_clicked(self):
        self.export_controller.cancel()
        if self.export_thread and self.export_thread.isRunning():
            self.status_label.setText("취소 중…")
        else:
            self.status_label.setText("취소됨 – 임시 파일이 정리되었습니다.")
            self.progress_bar.setVisible(False)
            self.btn_cancel.setVisible(False)
            self.btn_save.setEnabled(True)
            self.btn_save.setVisible(True)

    @Slot()
    def _on_retry_clicked(self):
        if not self.current_package or not self.export_controller.failures:
            return
        self.btn_retry.setVisible(False)
        self.btn_save_incomplete.setVisible(False)
        self.status_label.setText("재시도 중…")
        self.progress_bar.setVisible(True)
        total = len(self.current_package.items)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(len(self.export_controller.successes))

        def after_ua(ua: str):
            referer = self.current_package.source_url if self.current_package else HOME_URL

            def after_cookies(cookies: str):
                self.export_thread = QThread(self)
                w = RetryWorker(self.export_controller, ua, referer, cookies)
                w.moveToThread(self.export_thread)
                self.export_thread.started.connect(w.run)
                w.prog.connect(self._on_export_progress)
                w.done.connect(self._on_export_finished)
                w.fail.connect(self._on_export_failed)
                w.done.connect(self.export_thread.quit)
                w.fail.connect(self.export_thread.quit)
                self.export_thread.finished.connect(lambda: w.deleteLater())
                self.export_thread.finished.connect(self._cleanup_thread)
                self.export_thread.start()

            self.webview.get_cookies_string(after_cookies)

        self.webview.get_user_agent(after_ua)

    @Slot()
    def _on_save_incomplete_clicked(self):
        if not self.current_package or not self.current_destination:
            return
        reply = QMessageBox.question(
            self,
            "불완전 ZIP 저장",
            f"{len(self.export_controller.failures)}개 항목이 누락된 불완전 ZIP을 저장하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        result = self.export_controller.pack(incomplete=True)
        if result:
            self._on_export_finished(result)
        else:
            self.status_label.setText("불완전 ZIP 생성 실패")

    @Slot()
    def _on_show_in_finder(self):
        if not self.current_destination:
            return
        p = Path(self.current_destination)
        try:
            subprocess.run(["open", "-R", str(p)], check=False)
        except Exception:
            QMessageBox.information(self, "저장 위치", str(p))

    def _show_first_run_notice_if_needed(self):
        from PySide6.QtCore import QSettings

        settings = QSettings("dccon", "dccon-macos")
        if not settings.value("firstRunNoticeShown", False, type=bool):
            QMessageBox.information(
                self,
                "안내",
                "이 앱은 디시인사이드의 공식 앱이 아닌 디시콘 저장 도구입니다.\n"
                "디시콘의 저작권과 기타 권리는 각 제작자 및 정당한 권리자에게 있습니다.\n"
                "저장·이용 권한이 있는 콘텐츠에만 사용하고, 권리자의 허락이나 법적 근거 없이 "
                "재배포·판매하지 마세요. 구매·다운로드만으로 재배포 권한을 얻는 것은 아닙니다.\n"
                "사이트 이용약관과 콘텐츠별 이용 조건을 준수하고 접근 제한을 우회하지 마세요.\n"
                "자세한 내용은 README의 ‘저작권 및 이용 안내’를 확인하세요.",
            )
            settings.setValue("firstRunNoticeShown", True)

    def closeEvent(self, event):
        try:
            self.export_controller.cancel()
            self.export_controller.cleanup_on_app_exit()
            if self.webview._profile:
                self.webview._profile.clearAllVisitedLinks()
        except Exception:
            pass
        super().closeEvent(event)
