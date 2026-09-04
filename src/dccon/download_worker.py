"""DownloadWorker – §9 다운로드 정책 (UI 스레드 분리)."""

from __future__ import annotations

import logging
import os
import random
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, parse_qs
import http.client
import http.cookiejar
import urllib.request
import urllib.error

from .models import DcconItem, DownloadedItem, DownloadFailure
from .validators import validate_image, classify_retry_category, ValidationError

logger = logging.getLogger(__name__)


@dataclass
class DownloadProgress:
    completed: int
    total: int
    failed: int


def _mask_no(url: str) -> str:
    """로그용 마스킹: no 파라미터 앞뒤 일부만 노출 – §13."""
    try:
        p = urlparse(url)
        qs = parse_qs(p.query)
        no_vals = qs.get("no", [])
        if no_vals:
            v = no_vals[0]
            if len(v) <= 4:
                masked = "***"
            else:
                masked = v[:2] + "***" + v[-2:]
            return url.replace(v, masked)
        return url
    except Exception:
        return url


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        secs = int(value)
        if 0 <= secs <= 3600:
            return float(secs)
    except ValueError:
        pass
    # HTTP-date는 MVP에서 지원하지 않음 (초 단위 정수만)
    return None


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


def _download_one(
    item: DcconItem,
    temp_dir: Path,
    user_agent: str,
    referer: str,
    cookies: str | None,
    timeout: float,
    max_retries: int = 3,
    cancel_token: CancelToken | None = None,
) -> DownloadedItem | DownloadFailure:
    """단일 항목 다운로드 + 재시도. 성공 시 DownloadedItem, 실패 시 DownloadFailure."""
    url = item.url
    attempts = 0
    last_category = "unknown"
    last_message = ""
    last_status: int | None = None

    # 동시 요청 수 동적 조절용은 ExportController에서 관리 (429 시 1로)
    # 여기서는 개별 요청 재시도 로직만.

    base_delays = [1.0, 2.0, 4.0]

    while attempts <= max_retries:
        if cancel_token and cancel_token.cancelled:
            return DownloadFailure(item=item, category="cancelled", message="취소됨", attempts=attempts)

        attempts += 1
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", user_agent)
            req.add_header("Referer", referer)
            if cookies:
                req.add_header("Cookie", cookies)
            # 타임아웃
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                headers = resp.headers
                content_type = headers.get("Content-Type")
                retry_after = headers.get("Retry-After")
                data = resp.read()

                if status == 429:
                    ra = _parse_retry_after(retry_after)
                    last_category = "rate_limited"
                    last_message = f"429 Rate Limited (Retry-After={retry_after})"
                    last_status = status
                    if attempts <= max_retries:
                        delay = ra if ra is not None else base_delays[min(attempts - 1, len(base_delays) - 1)]
                        delay += random.uniform(0, 0.5)
                        logger.info("429 재시도 %s after %.1fs url=%s", attempts, delay, _mask_no(url))
                        # 취소 토큰 확인하며 대기
                        slept = 0.0
                        while slept < delay:
                            if cancel_token and cancel_token.cancelled:
                                return DownloadFailure(item=item, category="cancelled", message="취소됨", attempts=attempts)
                            time.sleep(min(0.1, delay - slept))
                            slept += 0.1
                        continue
                    else:
                        break

                # 검증
                try:
                    fmt = validate_image(data, content_type, status_code=status)
                except ValidationError as ve:
                    last_category = classify_retry_category(status, None)
                    # HTML 오류 페이지 등은 not retryable로 간주할 수 있으나 spec상 200 검증 실패는 재추출 폴백 대상
                    last_message = str(ve)
                    last_status = status
                    logger.warning("이미지 검증 실패 %s ct=%s url=%s err=%s", _mask_no(url), content_type, ve, ve)
                    # 검증 실패는 즉시 실패로 (재시도해도 같은 URL이므로 의미 없음) – 폴백은 상위에서
                    break

                # 성공: 임시 파일로 기록 (메모리 장기 보관 금지)
                ext = fmt  # 'png' | 'gif'
                fd, tmp_path_str = tempfile.mkstemp(suffix=f".{ext}", dir=str(temp_dir))
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
                tmp_path = Path(tmp_path_str)
                return DownloadedItem(
                    item=item,
                    temporary_path=tmp_path,
                    image_format=fmt,
                    byte_count=len(data),
                )

        except urllib.error.HTTPError as e:
            status = e.code
            headers = e.headers
            content_type = headers.get("Content-Type") if headers else None
            last_status = status
            last_category = classify_retry_category(status)
            body_preview = ""
            try:
                body_preview = e.read(512).decode("utf-8", errors="ignore")[:200]
            except Exception:
                pass
            last_message = f"HTTP {status} {body_preview}".strip()
            logger.warning("HTTPError %s url=%s body=%s", status, _mask_no(url), body_preview[:100])

            if status in (429,):
                # 위에서 처리되지만 urlopen이 예외를 던지는 경우 여기 도달
                retry_after = headers.get("Retry-After") if headers else None
                ra = _parse_retry_after(retry_after)
                if attempts <= max_retries:
                    delay = ra if ra is not None else base_delays[min(attempts - 1, len(base_delays) - 1)]
                    delay += random.uniform(0, 0.2)
                    time.sleep(delay)
                    continue
            elif status in (401, 403):
                # 호출자가 쿠키 동기화 후 재시도하도록 1회 재시도 (여기서는 단순 재시도)
                if attempts <= max_retries:
                    delay = base_delays[min(attempts - 1, len(base_delays) - 1)] + random.uniform(0, 0.3)
                    time.sleep(delay)
                    continue
            elif 500 <= status < 600:
                if attempts <= max_retries:
                    delay = base_delays[min(attempts - 1, len(base_delays) - 1)] + random.uniform(0, 0.5)
                    time.sleep(delay)
                    continue
            else:
                # 404 등은 폴백 대상 – 여기서는 실패 반환 (상위에서 재추출)
                break

        except urllib.error.URLError as e:
            # 타임아웃 포함
            is_timeout = "timed out" in str(e).lower() or isinstance(e.reason, TimeoutError) if hasattr(e, "reason") else False
            if is_timeout or "timeout" in str(e).lower():
                last_category = "timeout"
                last_message = f"타임아웃: {e}"
            else:
                last_category = "network"
                last_message = f"네트워크 오류: {e}"
            logger.warning("URLError url=%s err=%s", _mask_no(url), e)
            if attempts <= max_retries:
                delay = base_delays[min(attempts - 1, len(base_delays) - 1)] + random.uniform(0, 0.5)
                # 취소 대기
                slept = 0.0
                while slept < delay:
                    if cancel_token and cancel_token.cancelled:
                        return DownloadFailure(item=item, category="cancelled", message="취소됨", attempts=attempts)
                    time.sleep(min(0.1, delay - slept))
                    slept += 0.1
                continue
            break
        except Exception as e:
            last_category = "unknown"
            last_message = str(e)
            logger.exception("Unexpected download error url=%s", _mask_no(url))
            if attempts <= max_retries:
                delay = base_delays[min(attempts - 1, len(base_delays) - 1)]
                time.sleep(delay)
                continue
            break

    return DownloadFailure(item=item, category=last_category, message=last_message, attempts=attempts)


class DownloadWorker:
    """동시 4개 다운로드 – §9.

    사용법:
        worker = DownloadWorker(user_agent=..., referer=..., cookies=..., timeout=10)
        results = worker.download_all(items, temp_dir, progress_cb, cancel_token)
    """

    def __init__(
        self,
        user_agent: str,
        referer: str,
        cookies: str | None = None,
        timeout: float = 15.0,
        max_concurrency: int = 4,
    ) -> None:
        self.user_agent = user_agent
        self.referer = referer
        self.cookies = cookies
        self.timeout = timeout
        self.max_concurrency = max_concurrency
        self._executor: ThreadPoolExecutor | None = None

    def download_all(
        self,
        items: list[DcconItem],
        temp_dir: Path,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
        cancel_token: CancelToken | None = None,
        on_item_done: Callable[[DcconItem, DownloadedItem | DownloadFailure], None] | None = None,
    ) -> tuple[list[DownloadedItem], list[DownloadFailure]]:
        temp_dir.mkdir(parents=True, exist_ok=True)
        total = len(items)
        completed = 0
        failed = 0
        successes: list[DownloadedItem] = []
        failures: list[DownloadFailure] = []

        # 429 시 동시성 1로 낮추는 로직: 플래그
        lowered = False

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            self._executor = executor
            future_to_item: dict[Future, DcconItem] = {}
            for it in items:
                if cancel_token and cancel_token.cancelled:
                    break
                fut = executor.submit(
                    _download_one,
                    it,
                    temp_dir,
                    self.user_agent,
                    self.referer,
                    self.cookies,
                    self.timeout,
                    3,
                    cancel_token,
                )
                future_to_item[fut] = it

            for fut in as_completed(future_to_item):
                if cancel_token and cancel_token.cancelled:
                    # 남은 future 취소 시도
                    for f in future_to_item:
                        f.cancel()
                    break
                item = future_to_item[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    result = DownloadFailure(item=item, category="unknown", message=str(e), attempts=1)

                if isinstance(result, DownloadedItem):
                    successes.append(result)
                    completed += 1
                else:
                    failures.append(result)
                    if result.category == "rate_limited" and not lowered:
                        lowered = True
                        logger.info("429 감지로 동시성 저하 힌트 (다음 배치는 1로)")
                    failed += 1

                if on_item_done:
                    on_item_done(item, result)
                if progress_cb:
                    progress_cb(DownloadProgress(completed=completed, total=total, failed=failed))

                if cancel_token and cancel_token.cancelled:
                    break

        self._executor = None
        # 취소 시 성공한 임시 파일도 호출자가 정리해야 함
        return successes, failures
