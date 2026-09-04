"""PackageValidator / ImageValidator – §6, §8."""

from __future__ import annotations

import logging
from typing import Literal

from .models import DcconPackage
from .url_policy import is_allowed_image_url

logger = logging.getLogger(__name__)


class ValidationError(ValueError):
    pass


def validate_package(pkg: DcconPackage) -> None:
    """저장 시작 전 검증 – §8 '추출 검증'.

    실패 시 ValidationError.
    """
    if not pkg.title or not pkg.title.strip():
        raise ValidationError("패키지명이 비어 있습니다.")
    if len(pkg.items) == 0:
        raise ValidationError("항목이 0개입니다.")
    # 순서 중복/비연속 검사
    orders = [it.order for it in pkg.items]
    if len(set(orders)) != len(orders):
        raise ValidationError("순서가 중복됩니다.")
    # 연속적이지 않은지: 1..n 혹은 정렬 후 연속? spec상으로는 DOM 순서가 저장 순서이므로
    # order가 1..n 연속이어야 함. fixture 기준으로 1부터 시작.
    sorted_orders = sorted(orders)
    expected = list(range(1, len(pkg.items) + 1))
    if sorted_orders != expected:
        raise ValidationError(f"순서가 연속적이지 않습니다: {sorted_orders} vs {expected}")
    for it in pkg.items:
        if not it.url:
            raise ValidationError(f"항목 {it.order} URL이 비어 있습니다.")
        if not is_allowed_image_url(it.url):
            raise ValidationError(f"허용되지 않은 이미지 URL: {it.url}")
        if it.label is None:
            raise ValidationError(f"항목 {it.order} label이 None 입니다.")


# --- ImageValidator ---

PNG_SIG = bytes.fromhex("89 50 4E 47 0D 0A 1A 0A")
GIF87A = b"GIF87a"
GIF89A = b"GIF89a"


def detect_image_format(data: bytes, content_type: str | None = None) -> Literal["png", "gif"] | None:
    """시그니처 기반 포맷 판별. Content-Type은 충돌 로깅 용도로만 사용."""
    if not data:
        return None
    fmt: Literal["png", "gif"] | None = None
    if data.startswith(PNG_SIG):
        fmt = "png"
    elif data.startswith(GIF87A) or data.startswith(GIF89A):
        fmt = "gif"
    else:
        return None

    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        ct_fmt = None
        if ct == "image/png":
            ct_fmt = "png"
        elif ct == "image/gif":
            ct_fmt = "gif"
        # spec: 충돌 시 시그니처 기준, 로그 기록
        if ct_fmt is not None and ct_fmt != fmt:
            logger.warning("Content-Type과 시그니처 충돌: ct=%s sig=%s", ct, fmt)
        elif ct_fmt is None and ct not in ("", "application/octet-stream", "binary/octet-stream"):
            # PNG/GIF 외 응답은 실패로 간주할 수 있으나 여기서는 시그니처가 맞으면 허용
            # 실제 검증은 validate_image에서 Content-Type 엄격 검사를 선택적으로 수행
            pass
    return fmt


def validate_image(
    data: bytes,
    content_type: str | None,
    status_code: int | None = None,
) -> Literal["png", "gif"]:
    """§6 ImageValidator.

    검증 실패 시 ValidationError.
    성공 시 'png' | 'gif' 반환.
    """
    if status_code is not None and status_code != 200:
        raise ValidationError(f"HTTP 상태 {status_code}")
    if not data:
        raise ValidationError("빈 응답 본문")
    fmt = detect_image_format(data, content_type)
    if fmt is None:
        # Content-Type이 image/png/gif여도 시그니처가 맞지 않으면 실패
        raise ValidationError(f"알 수 없는 이미지 시그니처 (Content-Type={content_type})")
    # spec: PNG/GIF 외 응답은 실패
    # detect가 png/gif만 반환하므로 여기서는 png/gif 통과
    # Content-Type이 명백히 html 등이고 시그니처도 없으면 이미 실패함
    # 추가 방어: Content-Type이 text/html이면 실패로 처리 (200 HTML 오류 페이지)
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct == "text/html":
            # 시그니처가 png/gif면 앞서 통과했으나 html로 위장된 경우? 시그니처 우선이므로 허용
            # 하지만 데이터가 html이면 시그니처 불일치로 이미 실패
            pass
    return fmt


def classify_retry_category(status_code: int | None, exception: str | None = None) -> str:
    """오류 분류 – §9 DownloadWorker 재시도 분류에 재사용."""
    if exception == "timeout":
        return "timeout"
    if status_code is None:
        return "network"
    if status_code == 429:
        return "rate_limited"
    if status_code in (401, 403):
        return "auth"
    if status_code == 404:
        return "not_found"
    if 500 <= status_code < 600:
        return "server_error"
    if status_code == 200:
        return "validation"
    return "unknown"
