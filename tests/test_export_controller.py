"""단위 테스트 – ExportController 상태 머신."""

import tempfile
from pathlib import Path

import pytest

from dccon.export_controller import ExportController, ExportState
from dccon.models import DcconItem, DcconPackage


def make_pkg(n=2, title="테스트"):
    items = [
        DcconItem(order=i, label=f"l{i}", url=f"https://a.dcinside.com/dccon.php?no={i}")
        for i in range(1, n + 1)
    ]
    return DcconPackage(package_id="123", title=title, source_url="https://dccon.dcinside.com/#123", items=tuple(items))


def test_state_idle_to_extracting_to_failed_on_invalid():
    ctrl = ExportController()
    assert ctrl.state == ExportState.IDLE
    pkg_invalid = make_pkg(n=0)
    ok = ctrl.prepare(pkg_invalid, Path("/tmp/test.zip"), user_agent="ua", referer="ref", cookies=None)
    assert not ok
    assert ctrl.state == ExportState.FAILED


def test_prepare_success():
    ctrl = ExportController()
    pkg = make_pkg(n=2)
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out.zip"
        ok = ctrl.prepare(pkg, dest, user_agent="ua", referer="ref", cookies=None)
        assert ok
        assert ctrl.state == ExportState.EXTRACTING
        assert ctrl.temp_dir is not None
        assert ctrl.temp_dir.exists()
        ctrl.cleanup_on_app_exit()
        assert not ctrl.temp_dir or not ctrl.temp_dir.exists()


def test_cancel_resets_to_idle():
    ctrl = ExportController()
    pkg = make_pkg(n=2)
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out.zip"
        ctrl.prepare(pkg, dest, user_agent="ua", referer="ref", cookies=None)
        ctrl.cancel()
        assert ctrl.state == ExportState.IDLE
        # temp 정리
        assert ctrl.temp_dir is None


def test_cannot_start_when_busy():
    ctrl = ExportController()
    pkg = make_pkg(n=2)
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out.zip"
        ctrl.prepare(pkg, dest, user_agent="ua", referer="ref", cookies=None)
        ok2 = ctrl.prepare(pkg, dest, user_agent="ua", referer="ref", cookies=None)
        assert not ok2
        ctrl.cleanup_on_app_exit()
