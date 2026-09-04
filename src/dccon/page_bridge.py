"""PageBridge – QWebChannel 통신 – §6."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .models import DcconItem, DcconPackage

logger = logging.getLogger(__name__)


class PageBridge(QObject):
    """주입된 JS와 QWebChannel 사이 통신.

    - detailChanged: 경량 통지 (UI 활성화용)
    - extractFinished: 저장 직전 추출 결과
    """

    detailChanged = Signal(dict)  # {hasLayer, packageId, title, count}
    extractFinished = Signal(object)  # DcconPackage | str(error)
    extractFailed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    @Slot(str)
    def onDetailChanged(self, json_str: str) -> None:
        try:
            data = json.loads(json_str)
            self.detailChanged.emit(data)
        except Exception as e:
            logger.warning("onDetailChanged parse error: %s", e)

    def parse_extract_result(self, result: str | None, emit_signals: bool = True) -> DcconPackage | None:
        """runJavaScript 콜백에서 호출.

        result는 extract.js의 JSON 문자열. 파싱 후 DcconPackage 반환 또는 None.
        emit_signals=False 로 호출하면 시그널을 발생시키지 않고 반환만 한다.
        """
        if result is None:
            if emit_signals:
                self.extractFailed.emit("페이지 구조가 변경되어 디시콘을 읽을 수 없습니다.")
            return None
        try:
            if isinstance(result, dict):
                data = result
            else:
                data = json.loads(result)
        except Exception as e:
            logger.error("extract parse error: %s raw=%r", e, result)
            if emit_signals:
                self.extractFailed.emit("페이지 구조가 변경되어 디시콘을 읽을 수 없습니다.")
            return None

        if data.get("error") and not data.get("items"):
            err = data.get("error") or "상세 레이어가 없음"
            if emit_signals:
                self.extractFailed.emit(err)
            else:
                logger.warning("extract error (no emit): %s", err)
            return None

        package_id = data.get("package_id") or ""
        title = data.get("title") or ""
        source_url = data.get("source_url") or ""
        items_raw = data.get("items") or []

        items = []
        for it in items_raw:
            try:
                items.append(DcconItem(order=int(it["order"]), label=str(it.get("label") or ""), url=str(it["url"])))
            except Exception as e:
                logger.warning("skip malformed item %r: %s", it, e)
                continue

        pkg = DcconPackage(
            package_id=str(package_id),
            title=str(title),
            source_url=str(source_url),
            items=tuple(items),
        )
        if emit_signals:
            self.extractFinished.emit(pkg)
        return pkg

    @staticmethod
    def load_extract_js() -> str:
        scripts = Path(__file__).with_name("js")
        return (scripts / "detail_dom.js").read_text(encoding="utf-8") + "\n" + (scripts / "extract.js").read_text(encoding="utf-8")

    @staticmethod
    def load_observer_js() -> str:
        scripts = Path(__file__).with_name("js")
        return (scripts / "detail_dom.js").read_text(encoding="utf-8") + "\n" + (scripts / "observer.js").read_text(encoding="utf-8")
