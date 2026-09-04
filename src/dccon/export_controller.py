"""ExportController – §6 상태 머신."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from .download_worker import CancelToken, DownloadWorker
from .models import DcconPackage, DownloadedItem, DownloadFailure
from .validators import validate_package
from .zip_exporter import export_zip

logger = logging.getLogger(__name__)


class ExportState(Enum):
    IDLE = auto()
    EXTRACTING = auto()
    CHOOSING_DESTINATION = auto()
    DOWNLOADING = auto()
    PACKING = auto()
    COMPLETE = auto()
    FAILED = auto()


@dataclass
class ExportProgress:
    state: ExportState
    completed: int = 0
    total: int = 0
    message: str = ""


class ExportController:
    """UI와 다운로드/ZIP 계층 사이 유일한 조정자 – §6.

    상태 전이:
        IDLE -> EXTRACTING -> CHOOSING_DESTINATION -> DOWNLOADING -> PACKING -> COMPLETE
                                          |                 |           |
                                          +-----------------+-----------+-> FAILED
    """

    def __init__(self) -> None:
        self.state: ExportState = ExportState.IDLE
        self.cancel_token: CancelToken | None = None
        self.temp_dir: Path | None = None
        self.package_snapshot: DcconPackage | None = None
        self.destination: Path | None = None
        self.successes: list[DownloadedItem] = []
        self.failures: list[DownloadFailure] = []
        self._on_progress: Callable[[ExportProgress], None] | None = None

    def set_progress_callback(self, cb: Callable[[ExportProgress], None]) -> None:
        self._on_progress = cb

    def _emit(self, state: ExportState, completed: int = 0, total: int = 0, message: str = "") -> None:
        self.state = state
        if self._on_progress:
            self._on_progress(ExportProgress(state=state, completed=completed, total=total, message=message))

    def can_start(self) -> bool:
        return self.state == ExportState.IDLE

    def prepare(
        self,
        package: DcconPackage,
        destination: Path,
        *,
        user_agent: str,
        referer: str,
        cookies: str | None,
    ) -> bool:
        """저장 시작 – 검증 및 상태 전이. 성공 시 True, 검증 실패 시 FAILED로 전이 후 False."""
        if not self.can_start():
            logger.warning("ExportController busy: %s", self.state)
            return False

        self._emit(ExportState.EXTRACTING, message="검증 중…")
        try:
            validate_package(package)
        except Exception as e:
            self._emit(ExportState.FAILED, message=str(e))
            return False

        self.package_snapshot = package
        self.destination = Path(destination)
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dccon_"))
        self.cancel_token = CancelToken()
        self.successes = []
        self.failures = []
        return True

    def start_download(
        self,
        user_agent: str,
        referer: str,
        cookies: str | None,
        timeout: float = 15.0,
    ) -> tuple[list[DownloadedItem], list[DownloadFailure]]:
        """DOWNLOADING 단계 실행. 호출자는 prepare()를 먼저 호출해야 함."""
        assert self.package_snapshot is not None
        assert self.temp_dir is not None
        assert self.cancel_token is not None

        total = len(self.package_snapshot.items)
        self._emit(ExportState.DOWNLOADING, completed=0, total=total, message="다운로드 중…")

        worker = DownloadWorker(
            user_agent=user_agent,
            referer=referer,
            cookies=cookies,
            timeout=timeout,
            max_concurrency=4,
        )

        def on_progress(p) -> None:
            self._emit(ExportState.DOWNLOADING, completed=p.completed, total=p.total, message=f"{p.completed}/{p.total}")

        successes, failures = worker.download_all(
            list(self.package_snapshot.items),
            self.temp_dir,
            progress_cb=on_progress,
            cancel_token=self.cancel_token,
        )

        # 취소 확인
        if self.cancel_token.cancelled:
            self._cleanup_temp()
            self._emit(ExportState.IDLE, message="취소됨")
            return successes, failures

        self.successes = successes
        self.failures = failures

        if failures:
            self._emit(ExportState.FAILED, completed=len(successes), total=total, message=f"{len(failures)}개 실패")
            return successes, failures

        # 모두 성공 시 PACKING
        return successes, failures

    def pack(self, *, incomplete: bool = False) -> Path | None:
        """PACKING 단계. incomplete=False면 실패가 있을 때 호출 불가."""
        assert self.package_snapshot is not None
        assert self.destination is not None
        assert self.temp_dir is not None

        if not incomplete and self.failures:
            logger.warning("pack() called with failures but incomplete=False")
            return None

        self._emit(ExportState.PACKING, message="ZIP 생성 중…")

        try:
            dest = self.destination
            if incomplete and self.failures:
                # _incomplete.zip 으로 저장 (§9)
                from .filename_policy import incomplete_zip_filename

                # 사용자가 destination을 이미 incomplete로 선택했다면 그대로 사용
                if "_incomplete" not in dest.name:
                    dest = dest.with_name(incomplete_zip_filename(self.package_snapshot.title))
            result = export_zip(
                self.package_snapshot.title,
                self.successes,
                dest,
                incomplete_failures=self.failures if incomplete else None,
            )
            # 성공 시 임시 디렉터리 삭제
            self._cleanup_temp()
            self._emit(ExportState.COMPLETE, message=str(result))
            return result
        except Exception as e:
            logger.exception("ZIP 생성 실패")
            self._emit(ExportState.FAILED, message=str(e))
            return None

    def pack_complete_if_all_succeeded(self) -> Path | None:
        if self.failures:
            return None
        return self.pack(incomplete=False)

    def cancel(self) -> None:
        if self.cancel_token:
            self.cancel_token.cancel()
        # 다운로드 중 취소는 start_download 루프에서 감지
        # 그 외 상태에서는 즉시 정리
        if self.state in (ExportState.EXTRACTING, ExportState.CHOOSING_DESTINATION, ExportState.PACKING):
            self._cleanup_temp()
            self._emit(ExportState.IDLE, message="취소됨")

    def reset(self) -> None:
        self._cleanup_temp()
        self.cancel_token = None
        self.package_snapshot = None
        self.destination = None
        self.successes = []
        self.failures = []
        self._emit(ExportState.IDLE)

    def retry_failed(
        self,
        user_agent: str,
        referer: str,
        cookies: str | None,
    ) -> tuple[list[DownloadedItem], list[DownloadFailure]]:
        """실패 항목만 재시도 – §9 '실패 항목 다시 시도'."""
        assert self.package_snapshot is not None
        assert self.temp_dir is not None
        assert self.cancel_token is not None
        if not self.failures:
            return self.successes, self.failures

        # 취소 토큰 리셋
        self.cancel_token = CancelToken()
        retry_items = [f.item for f in self.failures]
        # 성공은 유지, 실패만 재시도
        worker = DownloadWorker(user_agent=user_agent, referer=referer, cookies=cookies, timeout=15.0, max_concurrency=4)
        total_retry = len(retry_items)
        self._emit(ExportState.DOWNLOADING, completed=0, total=total_retry, message="재시도 중…")

        successes2, failures2 = worker.download_all(retry_items, self.temp_dir, cancel_token=self.cancel_token)

        # 기존 성공에 추가
        self.successes.extend(successes2)
        self.failures = failures2

        if failures2:
            self._emit(ExportState.FAILED, completed=len(self.successes), total=len(self.package_snapshot.items))
        else:
            self._emit(ExportState.DOWNLOADING, completed=len(self.successes), total=len(self.package_snapshot.items))

        return self.successes, self.failures

    def _cleanup_temp(self) -> None:
        if self.temp_dir and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass
        self.temp_dir = None

    def cleanup_on_app_exit(self) -> None:
        self._cleanup_temp()
