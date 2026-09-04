"""단위 테스트 – PackageValidator / ImageValidator / url_policy."""

import pytest

from dccon.models import DcconItem, DcconPackage
from dccon.validators import (
    ValidationError,
    validate_package,
    validate_image,
    detect_image_format,
    classify_retry_category,
    PNG_SIG,
    GIF87A,
    GIF89A,
)
from dccon.url_policy import is_allowed_image_url, is_allowed_main_url


def make_pkg(title="테스트", items=None, package_id="123"):
    if items is None:
        items = [
            DcconItem(order=1, label="a", url="https://test.dcinside.com/dccon.php?no=abc123"),
            DcconItem(order=2, label="b", url="https://test.dcinside.com/dccon.php?no=def456"),
        ]
    return DcconPackage(package_id=package_id, title=title, source_url="https://dccon.dcinside.com/#123", items=tuple(items))


def test_validate_package_success():
    validate_package(make_pkg())


def test_validate_package_empty_title():
    pkg = make_pkg(title="  ")
    with pytest.raises(ValidationError):
        validate_package(pkg)


def test_validate_package_zero_items():
    pkg = make_pkg(items=[])
    with pytest.raises(ValidationError, match="0개"):
        validate_package(pkg)


def test_validate_package_duplicate_order():
    items = [
        DcconItem(order=1, label="a", url="https://a.dcinside.com/dccon.php?no=1"),
        DcconItem(order=1, label="b", url="https://b.dcinside.com/dccon.php?no=2"),
    ]
    pkg = make_pkg(items=items)
    with pytest.raises(ValidationError, match="중복"):
        validate_package(pkg)


def test_validate_package_non_continuous():
    items = [
        DcconItem(order=1, label="a", url="https://a.dcinside.com/dccon.php?no=1"),
        DcconItem(order=3, label="b", url="https://b.dcinside.com/dccon.php?no=2"),
    ]
    pkg = make_pkg(items=items)
    with pytest.raises(ValidationError, match="연속적이지"):
        validate_package(pkg)


def test_validate_package_disallowed_url():
    items = [
        DcconItem(order=1, label="a", url="https://evil.com/dccon.php?no=1"),
    ]
    pkg = make_pkg(items=items)
    with pytest.raises(ValidationError, match="허용되지 않은"):
        validate_package(pkg)


def test_image_validator_png():
    data = PNG_SIG + b"\x00" * 10
    fmt = validate_image(data, "image/png", status_code=200)
    assert fmt == "png"


def test_image_validator_gif87():
    data = GIF87A + b"\x00" * 10
    fmt = validate_image(data, "image/gif", status_code=200)
    assert fmt == "gif"


def test_image_validator_gif89():
    data = GIF89A + b"\x00" * 10
    fmt = validate_image(data, "image/gif", status_code=200)
    assert fmt == "gif"


def test_image_validator_content_type_conflict():
    # 시그니처는 PNG인데 Content-Type은 gif – 시그니처 우선, 성공
    data = PNG_SIG + b"\x00"
    fmt = validate_image(data, "image/gif", status_code=200)
    assert fmt == "png"


def test_image_validator_empty():
    with pytest.raises(ValidationError, match="빈 응답"):
        validate_image(b"", "image/png", status_code=200)


def test_image_validator_unknown_sig():
    with pytest.raises(ValidationError):
        validate_image(b"not an image", "image/png", status_code=200)


def test_image_validator_truncated():
    # 잘린 이미지 – 빈 데이터가 아니지만 시그니처 없음
    with pytest.raises(ValidationError):
        validate_image(b"\x89PNG", "image/png", status_code=200)


def test_image_validator_html_error_page():
    html = b"<html><body>error</body></html>"
    with pytest.raises(ValidationError):
        validate_image(html, "text/html", status_code=200)


def test_image_validator_http_error():
    with pytest.raises(ValidationError, match="HTTP 상태"):
        validate_image(PNG_SIG, "image/png", status_code=404)


def test_detect_image_format():
    assert detect_image_format(PNG_SIG + b"xxx", "image/png") == "png"
    assert detect_image_format(GIF89A + b"xxx", "image/gif") == "gif"
    assert detect_image_format(b"xxx", "image/png") is None


def test_url_policy_allowed():
    assert is_allowed_image_url("https://test.dcinside.com/dccon.php?no=abc123")
    assert is_allowed_image_url("https://img.dcinside.com/dccon.php?no=123&foo=bar")
    assert not is_allowed_image_url("http://test.dcinside.com/dccon.php?no=abc")  # not https
    assert not is_allowed_image_url("https://test.dcinside.com/dccon.php")  # no 'no'
    assert not is_allowed_image_url("https://test.dcinside.com/other.php?no=abc")
    assert not is_allowed_image_url("https://evil-dcinside.com/dccon.php?no=abc")  # suffix trick
    assert not is_allowed_image_url("https://evil.com/dccon.php?no=abc")


def test_main_url_allowed():
    assert is_allowed_main_url("https://dccon.dcinside.com/")
    assert is_allowed_main_url("https://dccon.dcinside.com/board?x=1")
    assert not is_allowed_main_url("https://evil.com/")
    assert not is_allowed_main_url("http://dccon.dcinside.com/")
    assert not is_allowed_main_url("file:///etc/passwd")


def test_retry_category():
    assert classify_retry_category(None, "timeout") == "timeout"
    assert classify_retry_category(429) == "rate_limited"
    assert classify_retry_category(403) == "auth"
    assert classify_retry_category(500) == "server_error"
    assert classify_retry_category(404) == "not_found"
    assert classify_retry_category(200) == "validation"
