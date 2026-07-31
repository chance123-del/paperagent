from __future__ import annotations

"""Auditable insertion of user-supplied equation transcriptions."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import zipfile

from .archive_safety import safe_extract_zip


PLACEHOLDER_PATTERN = re.compile(r"\[(Eq\d+|Equation\d+|公式\d+)\]", re.IGNORECASE)
FORBIDDEN_COMMANDS = re.compile(r"\\(?:input|include|write|openout|read|catcode|usepackage|documentclass)\b", re.IGNORECASE)


@dataclass
class FormulaData:
    latex: dict[str, tuple[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _key(value: object) -> str:
    normalized = str(value or "").strip().strip("[]").lower().replace("equation", "eq").replace("公式", "eq")
    return re.sub(r"[\s_.()\-]+", "", normalized)


def _bundle_root(upload: str | None, workspace: Path) -> Path | None:
    if not upload:
        return None
    source = Path(upload)
    if source.suffix.lower() == ".json":
        return source.parent
    if source.suffix.lower() != ".zip":
        raise ValueError("Formula input must be a ZIP bundle or formulas.json file.")
    destination = workspace / "formula_bundle"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(source, "r") as archive:
        safe_extract_zip(archive, destination)
    return destination


def load_formulas(upload: str | None, workspace: Path) -> FormulaData:
    root = _bundle_root(upload, workspace)
    if not root:
        return FormulaData()
    candidates = list(root.rglob("formulas.json"))
    if len(candidates) != 1:
        raise ValueError("Formula bundle must contain exactly one formulas.json file.")
    payload = json.loads(candidates[0].read_text(encoding="utf-8"))
    entries = payload.get("formulas", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("formulas.json must contain a formulas array.")
    result = FormulaData()
    for entry in entries:
        if not isinstance(entry, dict):
            result.warnings.append("Formula bundle contains a non-object entry.")
            continue
        key = _key(entry.get("formula_id", entry.get("asset_id", "")))
        latex = str(entry.get("latex", "")).strip()
        tag = str(entry.get("tag", "")).strip()
        if not re.fullmatch(r"eq\d+", key):
            result.warnings.append(f"Formula entry has invalid formula_id: {entry.get('formula_id', '')}.")
        elif not latex:
            result.warnings.append(f"{key}: handwritten image needs confirmed LaTeX before it can be inserted.")
        elif FORBIDDEN_COMMANDS.search(latex):
            result.warnings.append(f"{key}: LaTeX contains a disallowed document-level command.")
        elif key in result.latex:
            result.warnings.append(f"{key}: duplicate formula_id.")
        else:
            result.latex[key] = (latex, tag)
    return result


def apply_formulas(tex: str, formulas: FormulaData) -> tuple[str, list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    updated = tex
    for match in list(PLACEHOLDER_PATTERN.finditer(tex)):
        marker = match.group(0)
        key = _key(match.group(1))
        formula = formulas.latex.get(key)
        if not formula:
            missing.append(f"{marker}: no confirmed LaTeX formula was supplied.")
            continue
        latex, tag = formula
        tag_line = rf"\tag{{{tag}}}" if tag else ""
        block = "\n".join([r"\begin{equation}", latex, tag_line, r"\end{equation}"])
        updated = updated.replace(marker, block, 1)
        matched.append(f"{marker} -> {key}")
    if matched and not re.search(r"\\usepackage(?:\[[^]]+\])?\{amsmath\}", updated):
        updated = re.sub(r"(\\documentclass[^\n]*\n)", r"\1\\usepackage{amsmath}\n", updated, count=1)
    return updated, matched, missing


def write_formula_manifest(formulas: FormulaData, destination: Path) -> Path:
    path = destination / "formula_manifest.json"
    payload = {
        "formulas": [{"formula_id": key, "tag": tag} for key, (_, tag) in formulas.latex.items()],
        "warnings": formulas.warnings,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
