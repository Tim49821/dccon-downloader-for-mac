"""URL/호스트 검증 – §8, §12."""

from __future__ import annotations

from urllib.parse import urlparse, parse_qs


ALLOWED_MAIN_HOSTS = {
    "dccon.dcinside.com",
    "sign.dcinside.com",  # 로그인/로그아웃 및 인증 리다이렉트
    "sso.dcinside.com",  # 로그인 후 SSO 세션 연결 및 인증 복귀
    "mall.dcinside.com",  # 사이트 로그인 링크의 복귀 주소
}

# 이미지 CDN은 dcinside.com 하위. 실제 허용 목록은 확장 가능하나
# spec 상 "허용된 디시인사이드 이미지 CDN" -> dcinside.com 서브도메인으로 제한
ALLOWED_IMAGE_HOST_SUFFIX = ".dcinside.com"
# 또는 정확히 dcinside.com 자체도 허용 (방어)
ALLOWED_IMAGE_HOSTS_EXACT = {"dcinside.com"}

IMAGE_PATH = "/dccon.php"


def _is_dcinside_subdomain(host: str) -> bool:
    host = host.lower()
    if host in ALLOWED_IMAGE_HOSTS_EXACT:
        return True
    if host.endswith(ALLOWED_IMAGE_HOST_SUFFIX):
        # suffix 검사 + 도메인 경계: host가 '.dcinside.com'으로 끝나고
        # 그 앞이 '.' 경계임을 보장 (이미 suffix이 . 포함이므로 ok)
        # 추가 방어: 'evil-dcinside.com' 은 '.dcinside.com' 로 끝나지 않으므로 차단됨
        return True
    return False


def is_allowed_main_url(url: str) -> bool:
    """메인 프레임 탐색 허용 검사 – §12."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme != "https":
        return False
    host = (p.hostname or "").lower()
    if host in ALLOWED_MAIN_HOSTS:
        return True
    # 로그인 지원에 필요한 호스트만 명시적으로 허용한다.
    return False


def is_allowed_image_url(url: str) -> bool:
    """§8 이미지 노드 허용 조건 5가지."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme != "https":
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if not _is_dcinside_subdomain(host):
        return False
    if p.path != IMAGE_PATH:
        return False
    qs = parse_qs(p.query)
    if "no" not in qs or not qs["no"] or not qs["no"][0]:
        return False
    return True


def extract_no_param(url: str) -> str | None:
    try:
        p = urlparse(url)
        qs = parse_qs(p.query)
        vals = qs.get("no")
        if vals:
            return vals[0]
        return None
    except Exception:
        return None
