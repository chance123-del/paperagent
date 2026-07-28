from __future__ import annotations

from pathlib import Path
import re


def _infer_from_text(text: str, rules: dict, changes: list[str]) -> None:
    if re.search(r"\bFig(?:ure)?\.?\s*\d+", text, re.IGNORECASE):
        rules.setdefault("caption_prefixes", {})["figure"] = "Figure"
        changes.append("Figure captions detected")
    if re.search(r"\bTable\s*\d+", text, re.IGNORECASE):
        rules.setdefault("caption_prefixes", {})["table"] = "Table"
        changes.append("Table captions detected")
    references = text.split("References", 1)[-1]
    if re.search(r"(?m)^\s*\[\d+\]", references):
        rules["citation_style"] = "numeric"
        changes.append("Numeric reference labels detected")


def apply_reference_article_style(base_rules: dict, reference_path: str | None) -> tuple[dict, list[str]]:
    """Infer only robust, observable style cues from a public reference article."""
    rules = {**base_rules, "caption_prefixes": dict(base_rules.get("caption_prefixes", {}))}
    if not reference_path:
        return rules, []
    source = Path(reference_path)
    changes: list[str] = []
    if source.suffix.lower() == ".docx":
        from docx import Document

        document = Document(source)
        section = document.sections[0]
        margins = [section.top_margin.cm, section.bottom_margin.cm, section.left_margin.cm, section.right_margin.cm]
        if all(value > 0 for value in margins):
            rules["geometry"] = "a4paper,top={:.2f}cm,bottom={:.2f}cm,left={:.2f}cm,right={:.2f}cm".format(*margins)
            changes.append("Word page margins copied")
        _infer_from_text("\n".join(paragraph.text for paragraph in document.paragraphs), rules, changes)
    elif source.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(source))
        first_page = reader.pages[0]
        width, height = float(first_page.mediabox.width), float(first_page.mediabox.height)
        if abs(width - 595) < 8 and abs(height - 842) < 8:
            rules["geometry"] = "a4paper,margin=2.54cm"
            changes.append("A4 page size detected")
        _infer_from_text("\n".join(page.extract_text() or "" for page in reader.pages[:3]), rules, changes)
    else:
        raise ValueError("Reference article must be a PDF or Word (.docx) file.")
    return rules, list(dict.fromkeys(changes))
