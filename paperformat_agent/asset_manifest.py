from __future__ import annotations

"""Create auditable records for assets considered during placeholder matching."""

import csv
import hashlib
import json
from pathlib import Path
from typing import Iterable

from .placeholders import PlaceholderAsset


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_asset_manifest(
    bundle_dir: Path,
    placeholders: Iterable[tuple[str, str]],
    assets: dict[str, list[PlaceholderAsset]],
    ignored: Iterable[str],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    referenced_keys = {key for _, key in placeholders}
    for marker, key in placeholders:
        candidates = assets.get(key, [])
        status = "matched" if len(candidates) == 1 else "missing" if not candidates else "duplicate"
        for asset in candidates or [None]:
            source = asset.source if asset else None
            records.append({
                "placeholder": marker,
                "asset_id": key,
                "status": status,
                "kind": asset.kind if asset else "",
                "source_file": str(source.relative_to(bundle_dir)) if source else "",
                "sha256": _sha256(source) if source else "",
                "bytes": source.stat().st_size if source else "",
            })
    for key, candidates in assets.items():
        if key in referenced_keys:
            continue
        for asset in candidates:
            records.append({
                "placeholder": "",
                "asset_id": key,
                "status": "unreferenced",
                "kind": asset.kind,
                "source_file": str(asset.source.relative_to(bundle_dir)),
                "sha256": _sha256(asset.source),
                "bytes": asset.source.stat().st_size,
            })
    for name in ignored:
        records.append({"placeholder": "", "asset_id": "", "status": "ignored", "kind": "", "source_file": name, "sha256": "", "bytes": ""})
    return records


def write_asset_manifest(records: list[dict[str, object]], destination_dir: Path) -> tuple[Path, Path]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    json_path = destination_dir / "asset_manifest.json"
    csv_path = destination_dir / "asset_manifest.csv"
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["placeholder", "asset_id", "status", "kind", "source_file", "sha256", "bytes"]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    return json_path, csv_path
