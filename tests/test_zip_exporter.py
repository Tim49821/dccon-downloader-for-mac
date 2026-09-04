"""ZIP 스냅샷 테스트 – §14."""

import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from dccon.models import DcconItem, DownloadedItem
from dccon.zip_exporter import export_zip
from dccon.validators import PNG_SIG, GIF89A


def _make_downloaded(tmpdir: Path, order: int, label: str, fmt: str, total: int) -> DownloadedItem:
    data = (PNG_SIG if fmt == "png" else GIF89A) + b"\x00" * 10
    p = tmpdir / f"{order}.{fmt}"
    p.write_bytes(data)
    item = DcconItem(order=order, label=label, url=f"https://test.dcinside.com/dccon.php?no={order}")
    return DownloadedItem(item=item, temporary_path=p, image_format=fmt, byte_count=len(data))


def test_zip_structure_single_folder():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img_dir = Path(tempfile.mkdtemp())
        d1 = _make_downloaded(img_dir, 1, "웃음", "png", 2)
        d2 = _make_downloaded(img_dir, 2, "눈물", "gif", 2)
        dest = td / "테스트.zip"
        ts = datetime(2025, 1, 1, 12, 0, 0)
        export_zip("테스트", [d1, d2], dest, timestamp=ts)
        assert dest.exists()
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
            # 최상위 폴더 엔트리 명시적 기록
            assert "테스트/" in names
            assert "테스트/01_웃음.png" in names
            assert "테스트/02_눈물.gif" in names
            # ZIP_STORED
            for info in zf.infolist():
                if not info.is_dir():
                    assert info.compress_type == zipfile.ZIP_STORED
                    # 타임스탬프 통일
                    assert info.date_time == (2025, 1, 1, 12, 0, 0)
                    # UTF-8 플래그
                    assert info.flag_bits & 0x800
            # 추가 메타데이터 없음
            assert len([n for n in names if not n.startswith("테스트/")]) == 0


def test_zip_order_width_65():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img_dir = Path(tempfile.mkdtemp())
        items = [_make_downloaded(img_dir, i, f"l{i}", "png", 65) for i in range(1, 66)]
        dest = td / "패키지.zip"
        export_zip("패키지", items, dest, timestamp=datetime(2025, 1, 1, 0, 0, 0))
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
            assert "패키지/01_l1.png" in names
            assert "패키지/65_l65.png" in names
            assert "패키지/1_l1.png" not in names  # 두 자리여야 함


def test_zip_order_width_112():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img_dir = Path(tempfile.mkdtemp())
        items = [_make_downloaded(img_dir, i, f"l{i}", "png", 112) for i in range(1, 113)]
        dest = td / "대형.zip"
        export_zip("대형", items, dest, timestamp=datetime(2025, 1, 1, 0, 0, 0))
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
            assert "대형/001_l1.png" in names
            assert "대형/112_l112.png" in names
            assert "대형/01_l1.png" not in names


def test_zip_mixed_and_empty_label():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img_dir = Path(tempfile.mkdtemp())
        d1 = _make_downloaded(img_dir, 1, "a", "png", 2)
        d2 = _make_downloaded(img_dir, 2, "", "gif", 2)  # 빈 레이블 -> 02.gif
        dest = td / "혼합.zip"
        export_zip("혼합", [d1, d2], dest, timestamp=datetime(2025, 1, 1, 0, 0, 0))
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
            assert "혼합/01_a.png" in names
            assert "혼합/02.gif" in names


def test_zip_no_extra_metadata():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img_dir = Path(tempfile.mkdtemp())
        d1 = _make_downloaded(img_dir, 1, "x", "png", 1)
        dest = td / "단일.zip"
        export_zip("단일", [d1], dest, timestamp=datetime(2025, 1, 1, 0, 0, 0))
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
            # 정상 ZIP에 _download_errors.txt 없어야 함
            assert not any("_download_errors" in n for n in names)
            assert not any(n.endswith(".json") for n in names)


def test_incomplete_zip_has_error_file():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img_dir = Path(tempfile.mkdtemp())
        d1 = _make_downloaded(img_dir, 1, "ok", "png", 2)
        from dccon.models import DcconItem, DownloadFailure

        failure = DownloadFailure(
            item=DcconItem(order=2, label="fail", url="https://test.dcinside.com/dccon.php?no=999"),
            category="not_found",
            message="404",
            attempts=3,
        )
        dest = td / "불완전.zip"
        export_zip("불완전", [d1], dest, timestamp=datetime(2025, 1, 1, 0, 0, 0), incomplete_failures=[failure])
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
            assert "불완전/_download_errors.txt" in names
            content = zf.read("불완전/_download_errors.txt").decode("utf-8")
            assert "2" in content
            assert "fail" in content


def test_atomic_part_file():
    # .part 파일이 남지 않는지
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img_dir = Path(tempfile.mkdtemp())
        d1 = _make_downloaded(img_dir, 1, "a", "png", 1)
        dest = td / "원자.zip"
        export_zip("원자", [d1], dest, timestamp=datetime(2025, 1, 1, 0, 0, 0))
        # .part 파일이 같은 디렉터리에 남아있지 않아야 함
        parts = list(td.glob("*.part"))
        assert parts == []
