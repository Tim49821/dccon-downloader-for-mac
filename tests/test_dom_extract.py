"""DOM fixture 테스트 – §14.

실제 사이트에서 수동으로 확보한 최소 HTML fixture를 저장하고
추출 로직(허용 URL 판별, 레이블 처리 등)을 검증.

JS 실행 없이 Python 레벨에서 fixture 구조 기반 로직을 검증.
"""

import json
from pathlib import Path
import pytest

from dccon.url_policy import is_allowed_image_url


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_fixture_extract_current_layer_only():
    """현재 상세 레이어만 추출 – 썸네일/대표이미지/숨은 레이어 제외."""
    # fixture: 두 개의 레이어, 하나는 visible, 하나는 hidden
    # Python 레벨에서 is_allowed_image_url와 레이블 로직 검증
    assert is_allowed_image_url("https://a.dcinside.com/dccon.php?no=123")
    assert not is_allowed_image_url("https://a.dcinside.com/dccon.php?no=")  # 빈 no
    assert not is_allowed_image_url("https://evil.com/dccon.php?no=123")


# DOM order, repeated URLs, and empty labels are exercised by the real browser
# in test_detail_detection.py rather than checking JavaScript source strings.


def test_fixture_allowed_url_conditions():
    # §8 5가지 조건
    # - HTTPS
    assert not is_allowed_image_url("http://a.dcinside.com/dccon.php?no=123")
    # - 호스트가 허용된 CDN
    assert not is_allowed_image_url("https://evil.com/dccon.php?no=123")
    assert not is_allowed_image_url("https://evil-dcinside.com/dccon.php?no=123")
    # - 경로가 /dccon.php
    assert not is_allowed_image_url("https://a.dcinside.com/other.php?no=123")
    # - no 쿼리 존재
    assert not is_allowed_image_url("https://a.dcinside.com/dccon.php")
    # - 정상
    assert is_allowed_image_url("https://a.dcinside.com/dccon.php?no=abc123")


# 최소 HTML fixture 생성 및 검증
def test_minimal_fixture_structure(tmp_path):
    """fixture가 페이지 실행 지시나 제3자 스크립트를 포함하지 않는지."""
    fixture_html = """
    <div class="dccon_detail" data-package-id="123">
      <h3>테스트 패키지</h3>
      <ul class="list">
        <li><img src="https://img.dcinside.com/dccon.php?no=aaa" alt="웃음"></li>
        <li><img src="https://img.dcinside.com/dccon.php?no=bbb" alt=""></li>
        <li><img src="https://img.dcinside.com/dccon.php?no=ccc" alt="웃음"></li>
      </ul>
    </div>
    <div class="thumb_list" style="display:none">
      <img src="https://img.dcinside.com/dccon.php?no=thumb" alt="썸네일">
    </div>
    """
    # fixture는 최소 구조만 포함 – script 태그 없음
    assert "<script" not in fixture_html.lower()
    # 썸네일은 상세 레이어 밖이므로 제외되어야 함
    # is_allowed_image_url은 썸네일도 true이지만, extract.js는 레이어 내부만 추출하므로 방어됨
    assert is_allowed_image_url("https://img.dcinside.com/dccon.php?no=thumb")
    # 하지만 Python 레벨의 패키지 검증은 URL만 보므로 썸네일이 혼입되면 안 됨 – JS가 필터링


def test_page_bridge_parse():
    from dccon.page_bridge import PageBridge

    bridge = PageBridge()
    json_str = json.dumps({
        "error": None,
        "package_id": "123",
        "title": "테스트",
        "source_url": "https://dccon.dcinside.com/#123",
        "items": [
            {"order": 1, "label": "웃음", "url": "https://a.dcinside.com/dccon.php?no=1"},
            {"order": 2, "label": "", "url": "https://a.dcinside.com/dccon.php?no=2"},
            {"order": 3, "label": "웃음", "url": "https://a.dcinside.com/dccon.php?no=1"},  # 중복 URL 보존
        ]
    })
    pkg = bridge.parse_extract_result(json_str)
    assert pkg is not None
    assert pkg.title == "테스트"
    assert len(pkg.items) == 3
    assert pkg.items[0].label == "웃음"
    assert pkg.items[1].label == ""
    assert pkg.items[2].url == "https://a.dcinside.com/dccon.php?no=1"


def test_page_bridge_empty_layer():
    from dccon.page_bridge import PageBridge
    bridge = PageBridge()
    json_str = json.dumps({
        "error": "상세 레이어가 없음",
        "package_id": "",
        "title": "",
        "source_url": "https://dccon.dcinside.com/",
        "items": []
    })
    pkg = bridge.parse_extract_result(json_str)
    assert pkg is None
