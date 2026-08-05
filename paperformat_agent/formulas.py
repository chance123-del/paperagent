from __future__ import annotations

"""Auditable insertion of user-supplied equation transcriptions."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import zipfile

from .archive_safety import safe_extract_zip


PLACEHOLDER_PATTERN = re.compile(
    r"\[(?:EQ:([A-Za-z0-9][A-Za-z0-9_.-]{0,63})|(Eq\d+|Equation\d+|公式\d+))\]",
    re.IGNORECASE,
)
FORBIDDEN_COMMANDS = re.compile(
    r"\\(?:(?:input|include|write|write18|openout|read|catcode|usepackage|documentclass|"
    r"newcommand|renewcommand|def|csname|special|immediate)\b|begin\s*\{document\}|end\s*\{document\})",
    re.IGNORECASE,
)
SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,31}")


@dataclass
class FormulaData:
    latex: dict[str, tuple[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _key(value: object) -> str:
    normalized = str(value or "").strip().strip("[]").lower().replace("equation", "eq").replace("公式", "eq")
    return re.sub(r"[\s_.()\-]+", "", normalized)


def _formula_file(upload: str | None, workspace: Path) -> Path | None:
    if not upload:
        return None
    source = Path(upload)
    if source.suffix.lower() == ".json":
        return source
    if source.suffix.lower() != ".zip":
        raise ValueError("公式合集必须是 ZIP 或 JSON 文件。")
    destination = workspace / "formula_bundle"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(source, "r") as archive:
        safe_extract_zip(archive, destination)
    candidates = list(destination.rglob("formulas.json"))
    if len(candidates) != 1:
        raise ValueError("公式 ZIP 中必须且只能包含一个 formulas.json。")
    return candidates[0]


def load_formulas(upload: str | None, workspace: Path) -> FormulaData:
    formula_file = _formula_file(upload, workspace)
    if formula_file is None:
        return FormulaData()
    payload = json.loads(formula_file.read_text(encoding="utf-8"))
    entries = payload.get("formulas", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("formulas.json 必须包含 formulas 数组。")
    result = FormulaData()
    for entry in entries:
        if not isinstance(entry, dict):
            result.warnings.append("公式合集包含非对象条目。")
            continue
        key = _key(entry.get("formula_id", entry.get("asset_id", "")))
        latex = str(entry.get("latex", "")).strip()
        tag = str(entry.get("tag", "")).strip()
        if not re.fullmatch(r"eq[a-z0-9]+", key):
            result.warnings.append(f"公式编号无效：{entry.get('formula_id', '')}。")
        elif not latex:
            result.warnings.append(f"{key}：缺少经用户确认的 LaTeX，不能根据手写图片猜写公式。")
        elif FORBIDDEN_COMMANDS.search(latex):
            result.warnings.append(f"{key}：LaTeX 含有禁止的文档级或文件操作命令。")
        elif tag and not SAFE_TAG.fullmatch(tag):
            result.warnings.append(f"{key}：公式编号 tag 只能使用字母、数字、点和连字符。")
        elif key in result.latex:
            result.warnings.append(f"{key}：公式编号重复。")
        else:
            result.latex[key] = (latex, tag)
    return result


def apply_formulas(tex: str, formulas: FormulaData) -> tuple[str, list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    updated = tex
    for match in list(PLACEHOLDER_PATTERN.finditer(tex)):
        marker = match.group(0)
        key = _key(match.group(1) or match.group(2))
        formula = formulas.latex.get(key)
        if not formula:
            missing.append(f"{marker}：未提供经确认的 LaTeX 公式。")
            continue
        latex, tag = formula
        lines = [r"\begin{equation}", latex]
        if tag:
            lines.append(rf"\tag{{{tag}}}")
        lines.append(r"\end{equation}")
        updated = updated.replace(marker, "\n".join(lines), 1)
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
