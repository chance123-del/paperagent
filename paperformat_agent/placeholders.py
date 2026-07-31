from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import zipfile

from .archive_safety import safe_extract_zip
from .hybrid_insert import build_block, replace_placeholder


PLACEHOLDER_PATTERN = re.compile(r"\[(Fig\d+|Figure\d+|Table\d+|图\d+|表\d+)\]", re.IGNORECASE)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
TABLE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv", ".md"}


@dataclass
class PlaceholderAsset:
    key: str
    kind: str
    source: Path
    display_name: str


def _normalize_key(value: str) -> str:
    normalized = value.strip().strip("[]").lower()
    normalized = normalized.replace("figure", "fig")
    normalized = re.sub(r"[\s_\-().]+", "", normalized)
    normalized = normalized.replace("图", "fig")
    normalized = normalized.replace("表", "table")
    return normalized


def find_placeholders(tex: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for match in PLACEHOLDER_PATTERN.finditer(tex):
        marker = match.group(0)
        key = _normalize_key(match.group(1))
        if marker not in seen:
            results.append((marker, key))
            seen.add(marker)
    return results


def unpack_bundle(bundle_path: str | None, workspace_dir: Path) -> Path:
    if not bundle_path:
        raise ValueError("Upload a ZIP bundle that contains figures and tables named like Fig1, 图1, Table1, or 表1.")
    source = Path(bundle_path)
    destination = workspace_dir / "placeholder_bundle"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source, "r") as archive:
            safe_extract_zip(archive, destination)
        return destination
    copied = destination / source.name
    shutil.copy2(source, copied)
    return destination


def scan_assets(bundle_dir: Path) -> tuple[dict[str, list[PlaceholderAsset]], list[str]]:
    assets: dict[str, list[PlaceholderAsset]] = {}
    ignored: list[str] = []
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in IMAGE_EXTENSIONS | TABLE_EXTENSIONS:
            ignored.append(path.name)
            continue
        kind = "Figure" if suffix in IMAGE_EXTENSIONS else "Table"
        key = _normalize_key(path.stem)
        if not re.fullmatch(r"(fig|table)\d+", key):
            ignored.append(path.name)
            continue
        assets.setdefault(key, []).append(PlaceholderAsset(key=key, kind=kind, source=path, display_name=path.name))
    return assets, ignored


def apply_placeholder_assets(
    tex: str,
    bundle_dir: Path,
    project_dir: Path,
    rules: dict | None,
    figure_captions: dict[str, str] | None = None,
    table_captions: dict[str, str] | None = None,
    caption_links: dict[str, tuple[str, str]] | None = None,
) -> tuple[str, list[str], list[str], list[str]]:
    figure_captions = figure_captions or {}
    table_captions = table_captions or {}
    caption_links = caption_links or {}
    placeholders = find_placeholders(tex)
    assets, ignored = scan_assets(bundle_dir)
    updated = tex
    matched: list[str] = []
    missing: list[str] = []
    duplicate: list[str] = []

    for marker, key in placeholders:
        candidates = assets.get(key, [])
        if not candidates:
            missing.append(f"{marker} -> 未找到匹配素材")
            continue
        if len(candidates) > 1:
            duplicate.append(f"{marker} -> {', '.join(item.display_name for item in candidates)}")
            continue
        selected = candidates[0]
        caption_map = figure_captions if selected.kind == "Figure" else table_captions
        caption = caption_map.get(key, "")
        block = build_block(selected.kind, "", str(selected.source), caption, project_dir, rules, caption_links.get(key))
        updated = replace_placeholder(updated, marker, block)
        matched.append(f"{marker} -> {selected.display_name}")

    return updated, matched, missing, duplicate + ([f"忽略文件: {', '.join(ignored)}"] if ignored else [])


def parse_caption_lines(text: str) -> dict[str, str]:
    captions: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        label, caption = line.split(":", 1)
        key = _normalize_key(label)
        if re.fullmatch(r"(fig|table)\d+", key):
            captions[key] = caption.strip()
    return captions
