"""데이터 모델 – docs/superpowers/specs/2026-08-31-dccon-macos-design.md §7."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class DcconItem:
    order: int
    label: str
    url: str


@dataclass(frozen=True)
class DcconPackage:
    package_id: str
    title: str
    source_url: str
    items: tuple[DcconItem, ...]


@dataclass(frozen=True)
class DownloadedItem:
    item: DcconItem
    temporary_path: Path
    image_format: Literal["png", "gif"]
    byte_count: int


@dataclass(frozen=True)
class DownloadFailure:
    item: DcconItem
    category: str
    message: str
    attempts: int
