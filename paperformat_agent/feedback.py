from __future__ import annotations

from pathlib import Path
import re
import shutil


def apply_text_feedback(tex_text: str, feedback: str) -> tuple[str, list[str]]:
    """Apply unambiguous formatting requests while preserving a review trail."""
    normalized = feedback.lower()
    changes: list[str] = []
    mentions_table = any(token in normalized for token in ("table", "caption", "duplicate", "repeat", "colon", "\u8868", "\u91cd\u590d", "\u5192\u53f7"))
    if mentions_table:
        updated = re.sub(r"\\caption\{(?:Table|Figure)\s*[:\uff1a]\s*", r"\\caption{", tex_text)
        if updated != tex_text:
            tex_text = updated
            changes.append("Removed duplicated automatic figure/table labels from captions.")
    if "double space" in normalized or "extra space" in normalized:
        updated = re.sub(r"[ \t]{2,}", " ", tex_text)
        if updated != tex_text:
            tex_text = updated
            changes.append("Collapsed repeated spaces in LaTeX source.")
    return tex_text, changes


def apply_review_operation(tex_text: str, selected_text: str, action: str, value: str, url: str) -> tuple[str, list[str]]:
    selected = selected_text.strip()
    if not selected:
        return tex_text, []
    position = tex_text.find(selected)
    if position == -1:
        return tex_text, ["Selected text was not found in the current LaTeX source."]
    if action == "Correct selected text" and value.strip():
        updated = tex_text.replace(selected, value.strip(), 1)
        return updated, ["Replaced the selected text."]
    if action == "Insert hyperlink" and url.strip():
        linked = chr(92) + "href{" + url.strip() + "}{" + selected + "}"
        updated = tex_text.replace(selected, linked, 1)
        return updated, ["Inserted a hyperlink for the selected text."]
    if action == "Insert content after selection" and value.strip():
        updated = tex_text.replace(selected, selected + "\n\n" + value.strip(), 1)
        return updated, ["Inserted content after the selected text."]
    return tex_text, ["No valid review operation was supplied."]


def save_feedback_evidence(run_dir: Path, feedback_text: str, image_paths: list[str] | None) -> list[Path]:
    evidence_dir = run_dir / "review_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report = evidence_dir / "feedback.md"
    report.write_text("# Review Feedback\n\n" + (feedback_text.strip() or "No written feedback provided.") + "\n", encoding="utf-8")
    saved = [report]
    for index, image_path in enumerate(image_paths or [], start=1):
        source = Path(image_path)
        if source.exists() and source.is_file():
            destination = evidence_dir / f"issue-{index}{source.suffix.lower()}"
            shutil.copy2(source, destination)
            saved.append(destination)
    return saved
