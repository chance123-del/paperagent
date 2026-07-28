from __future__ import annotations

from pathlib import Path
import re


def extract_guideline_text(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(str(source)).pages)
    if suffix == ".docx":
        from docx import Document

        document = Document(source)
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    if suffix in {".md", ".markdown", ".txt"}:
        return source.read_text(encoding="utf-8", errors="replace")
    raise ValueError("Target requirements must be PDF, Word (.docx), Markdown, or text.")


def apply_guideline_overrides(base_rules: dict, guide_path: str | None) -> tuple[dict, list[str]]:
    """Apply only values stated unambiguously in a supplied target guide."""
    if not guide_path:
        return apply_requirement_text(base_rules, "")
    return apply_requirement_text(base_rules, extract_guideline_text(guide_path))


def apply_requirement_text(base_rules: dict, text: str) -> tuple[dict, list[str]]:
    """Apply explicit layout requirements entered by a user or extracted from a guide."""
    rules = dict(base_rules)
    if not text.strip():
        return rules, []
    changes: list[str] = []
    margin = re.search(r"(?:margin|margins|\u9875\u8fb9\u8ddd)[^\d]{0,20}(\d+(?:\.\d+)?)\s*(cm|mm|in)", text, re.IGNORECASE)
    if margin:
        value, unit = margin.groups()
        rules["geometry"] = f"a4paper,margin={value}{unit}"
        changes.append(f"Margin: {value}{unit}")

    spacing = re.search(r"(?:line\s*spacing|line\s*space|\u884c\u8ddd)[^\d]{0,20}([12](?:\.\d+)?)", text, re.IGNORECASE)
    if spacing:
        rules["line_spread"] = spacing.group(1)
        changes.append(f"Line spacing: {spacing.group(1)}")

    bibliography = re.search(r"(?:bibliography\s*style|reference\s*style)[^A-Za-z]{0,20}([A-Za-z][A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if bibliography:
        rules["bibliographystyle"] = bibliography.group(1)
        changes.append(f"Bibliography style: {bibliography.group(1)}")
    return rules, changes
