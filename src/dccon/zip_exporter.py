"""ZipExporter – §10, §11."""

from __future__ import annotations

import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .filename_policy import image_filename, safe_package_name
from .models import DownloadedItem, DownloadFailure


def _zip_info(name: str, dt: datetime) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name)
    zi.date_time = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    zi.compress_type = zipfile.ZIP_STORED
    # UTF-8 플래그
    zi.flag_bits |= 0x800
    # 외부 속성: 일반 파일 권한 0o644
    zi.external_attr = (0o644 << 16)
    return zi


def _dir_info(name: str, dt: datetime) -> zipfile.ZipInfo:
    if not name.endswith("/"):
        name = name + "/"
    zi = zipfile.ZipInfo(name)
    zi.date_time = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    zi.compress_type = zipfile.ZIP_STORED
    zi.flag_bits |= 0x800
    zi.external_attr = (0o755 << 16) | 0x10
    return zi


def export_zip(
    package_title: str,
    downloaded: list[DownloadedItem],
    destination: Path,
    *,
    timestamp: datetime | None = None,
    incomplete_failures: list[DownloadFailure] | None = None,
) -> Path:
    """검증된 임시 이미지로 ZIP 생성 – 원자적 확정.

    - destination과 같은 디렉터리에 .part 임시 파일 생성 후 rename
    - 최상위 폴더 엔트리 명시적 기록 (§10)
    - ZIP_STORED, 타임스탬프 통일
    - incomplete_failures가 있으면 _download_errors.txt 포함 (§9)
    """
    safe_title = safe_package_name(package_title)
    total = len(downloaded)
    if timestamp is None:
        timestamp = datetime.now()

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # 동일 파일시스템 원자성을 위해 같은 디렉터리에 temp 생성
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        suffix=".part", prefix=destination.stem + "_", dir=str(destination.parent)
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)

    try:
        # downloaded는 order 순으로 정렬되어야 함
        downloaded_sorted = sorted(downloaded, key=lambda d: d.item.order)

        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
            # 최상위 폴더
            zf.writestr(_dir_info(safe_title, timestamp), b"")

            for d in downloaded_sorted:
                ext = d.image_format  # 'png' | 'gif'
                fname = image_filename(d.item.order, total, d.item.label, ext)
                arcname = f"{safe_title}/{fname}"
                zi = _zip_info(arcname, timestamp)
                data = d.temporary_path.read_bytes()
                zf.writestr(zi, data)

            if incomplete_failures:
                lines = []
                for f in incomplete_failures:
                    lines.append(
                        f"{f.item.order}\t{f.item.label}\t{f.message} (attempts={f.attempts}, category={f.category})\n"
                    )
                content = "".join(lines).encode("utf-8")
                zi = _zip_info(f"{safe_title}/_download_errors.txt", timestamp)
                zf.writestr(zi, content)

        # 원자적 교체: macOS에서는 replace가 원자적
        tmp_path.replace(destination)
        return destination
    except Exception:
        # 실패 시 part 제거
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise
