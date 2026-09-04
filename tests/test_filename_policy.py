"""단위 테스트 – §14: FilenamePolicy."""

import unicodedata

from dccon.filename_policy import (
    order_width,
    format_order,
    image_filename,
    safe_package_name,
    safe_label,
    sanitize_component,
    zip_filename,
    incomplete_zip_filename,
)


def test_order_width():
    assert order_width(9) == 2
    assert order_width(65) == 2
    assert order_width(100) == 3
    assert order_width(112) == 3
    assert order_width(1) == 2
    assert order_width(99) == 2
    assert order_width(999) == 3


def test_format_order():
    assert format_order(1, 9) == "01"
    assert format_order(9, 9) == "09"
    assert format_order(1, 65) == "01"
    assert format_order(65, 65) == "65"
    assert format_order(1, 112) == "001"
    assert format_order(112, 112) == "112"
    assert format_order(5, 112) == "005"


def test_nfc_normalization():
    # e + combining acute -> é NFC
    decomposed = "e\u0301"
    assert sanitize_component(decomposed) == unicodedata.normalize("NFC", decomposed)


def test_forbidden_chars():
    assert "/" not in safe_package_name("a/b")
    assert ":" not in safe_package_name("a:b")
    assert safe_package_name("a/b:c") == "a_b_c"
    # NUL and control
    assert safe_package_name("a\x00b\x1f") == "a_b_"


def test_trailing_dots_and_spaces():
    assert safe_package_name("  hello...  ") == "hello"
    assert safe_package_name("  ...  ") == "untitled"


def test_empty_label():
    assert safe_label("") == ""
    assert safe_label("   ") == ""
    # "/" -> "_" is valid label segment (not empty)
    assert safe_label("/") == "_"
    assert safe_label("...") == ""  # only dots -> empty


def test_image_filename_with_label():
    assert image_filename(1, 65, "웃음", "png") == "01_웃음.png"
    assert image_filename(65, 65, "웃음", "gif") == "65_웃음.gif"


def test_image_filename_without_label():
    assert image_filename(1, 9, "", "png") == "01.png"
    assert image_filename(2, 9, "   ", "gif") == "02.gif"


def test_image_filename_sanitizes_label():
    # label with forbidden char
    assert image_filename(1, 9, "a/b", "png") == "01_a_b.png"
    assert image_filename(1, 9, "a:b", "png") == "01_a_b.png"


def test_zip_filenames():
    assert zip_filename("테스트") == "테스트.zip"
    assert incomplete_zip_filename("테스트") == "테스트_incomplete.zip"
    assert zip_filename("a/b") == "a_b.zip"


def test_duplicate_labels_no_collision():
    # 순번이 항상 포함되므로 충돌 없음
    f1 = image_filename(1, 9, "같은이름", "png")
    f2 = image_filename(2, 9, "같은이름", "png")
    assert f1 != f2


def test_untitled_fallback():
    assert sanitize_component("") == "untitled"
    assert sanitize_component("   ") == "untitled"
    assert sanitize_component("...") == "untitled"
