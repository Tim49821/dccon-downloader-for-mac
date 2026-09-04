"""FilenamePolicy – §10 ZIP 저장 규칙 (순수 함수)."""

from __future__ import annotations

import re
import unicodedata


# 파일을 시스템에서 금지하는 문자
_FORBIDDEN_RE = re.compile(r'[/:\x00-\x1f\x7f]')

# 윈도우 예약명은 아니지만 끝의 마침표/공백 제거 후 최대 호환성 확보
_TRAILING_DOTS_RE = re.compile(r"\.+$")


def normalize_text(value: str) -> str:
    """NFC 정규화."""
    return unicodedata.normalize("NFC", value)


def sanitize_component(name: str) -> str:
    """ZIP명/폴더명/레이블 세그먼트에 공통 적용할 정규화.

    - NFC
    - /, :, NUL, 제어문자 -> _
    - 앞뒤 공백 제거, 끝 마침표 제거
    - 결과가 비면 'untitled'
    """
    s = normalize_text(name)
    s = _FORBIDDEN_RE.sub("_", s)
    s = s.strip()
    s = _TRAILING_DOTS_RE.sub("", s).strip()
    # control chars already replaced but keep defensive
    if not s:
        return "untitled"
    # 길이 제한 (macOS HFS+ 255자 근사, 여유)
    if len(s) > 200:
        s = s[:200].rstrip(" .")
        if not s:
            return "untitled"
    return s


def safe_package_name(title: str) -> str:
    """패키지명 기반 ZIP/폴더명."""
    return sanitize_component(title)


def safe_label(label: str) -> str:
    """이미지 레이블 세그먼트 정규화. 빈 레이블이면 ''를 반환하여 호출자가 분기."""
    if label is None:
        return ""
    s = normalize_text(label)
    # alt가 공백뿐이면 빈 레이블
    if not s.strip():
        return ""
    s = _FORBIDDEN_RE.sub("_", s)
    s = s.strip()
    s = _TRAILING_DOTS_RE.sub("", s).strip()
    if not s:
        return ""
    if len(s) > 180:
        s = s[:180].rstrip(" .")
    return s


def order_width(total: int) -> int:
    """순번 너비: max(2, len(str(total))) – §10."""
    return max(2, len(str(total)))


def format_order(order: int, total: int) -> str:
    width = order_width(total)
    return str(order).zfill(width)


def image_filename(order: int, total: int, label: str, ext: str) -> str:
    """{순번}_{레이블}.{확장자} 또는 {순번}.{확장자}.

    ext는 'png' | 'gif' (점 없음). label은 원본 alt 값.
    """
    order_str = format_order(order, total)
    safe = safe_label(label)
    if safe:
        # 레이블 자체가 파일시스템에서 금지된 문자를 이미 처리함
        # 추가로 utf-8 플래그는 zip 생성 시 설정
        return f"{order_str}_{safe}.{ext}"
    return f"{order_str}.{ext}"


def zip_filename(title: str) -> str:
    return f"{safe_package_name(title)}.zip"


def incomplete_zip_filename(title: str) -> str:
    return f"{safe_package_name(title)}_incomplete.zip"
