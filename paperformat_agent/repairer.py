from __future__ import annotations

import re
from typing import Iterable

from .analyzer import KEYWORD_PATTERNS
from .models import RepairAction


BS = chr(92)


def _ensure_documentclass(text: str, rules: dict, actions: list[RepairAction]) -> str:
    documentclass = rules.get("documentclass")
    if not documentclass:
        return text
    options = rules.get("documentclass_options", "")
    replacement = f"{BS}documentclass[{options}]{{{documentclass}}}" if options else f"{BS}documentclass{{{documentclass}}}"
    pattern = re.compile(re.escape(BS) + r"documentclass(?:\[[^\]]*\])?\{[^}]*\}")
    new_text = pattern.sub(lambda _: replacement, text, count=1)
    if new_text != text:
        actions.append(RepairAction("documentclass", f"Applied target document class '{documentclass}'."))
    return new_text


def _ensure_geometry(text: str, geometry: str, actions: list[RepairAction]) -> str:
    pattern = re.compile(re.escape(BS) + r"usepackage(?:\[[^\]]*\])?\{geometry\}")
    replacement = f"{BS}usepackage[{geometry}]{{geometry}}"
    if pattern.search(text):
        new_text = pattern.sub(lambda _: replacement, text, count=1)
        if new_text != text:
            actions.append(RepairAction("geometry_mismatch", f"Updated geometry to '{geometry}'."))
        return new_text
    marker = BS + "begin{document}"
    if marker in text:
        text = text.replace(marker, replacement + "\n" + marker, 1)
        actions.append(RepairAction("geometry_mismatch", f"Inserted geometry package with '{geometry}'."))
    return text


def _ensure_linespread(text: str, line_spread: str, actions: list[RepairAction]) -> str:
    pattern = re.compile(re.escape(BS) + r"linespread\{[^}]*\}")
    replacement = f"{BS}linespread{{{line_spread}}}"
    if pattern.search(text):
        new_text = pattern.sub(lambda _: replacement, text, count=1)
        if new_text != text:
            actions.append(RepairAction("line_spread_mismatch", f"Updated line spread to '{line_spread}'."))
        return new_text
    marker = BS + "begin{document}"
    if marker in text:
        text = text.replace(marker, replacement + "\n" + marker, 1)
        actions.append(RepairAction("line_spread_mismatch", f"Inserted line spread '{line_spread}'."))
    return text


def _ensure_bibliographystyle(text: str, style: str, actions: list[RepairAction]) -> str:
    pattern = re.compile(re.escape(BS) + r"bibliographystyle\{[^}]*\}")
    replacement = f"{BS}bibliographystyle{{{style}}}"
    if pattern.search(text):
        new_text = pattern.sub(lambda _: replacement, text, count=1)
        if new_text != text:
            actions.append(RepairAction("bibliographystyle_mismatch", f"Updated bibliography style to '{style}'."))
        return new_text
    marker = BS + "bibliography{"
    index = text.find(marker)
    if index != -1:
        text = text[:index] + replacement + "\n" + text[index:]
        actions.append(RepairAction("bibliographystyle_mismatch", f"Inserted bibliography style '{style}'."))
    return text


def _build_block(template: str | Iterable[str]) -> str:
    return template.strip() if isinstance(template, str) else "\n".join(line.rstrip("\n") for line in template).strip()


def _insert_after_marker(text: str, marker: str, block: str) -> str:
    abstract_end = BS + "end{abstract}"
    if marker == "abstract" and abstract_end in text:
        return text.replace(abstract_end, abstract_end + "\n\n" + block, 1)
    resolved_marker = marker if marker in text else BS + "begin{document}"
    return text.replace(resolved_marker, resolved_marker + "\n\n" + block, 1) if resolved_marker in text else text


def _ensure_abstract(text: str, abstract_rules: dict, actions: list[RepairAction]) -> str:
    if not abstract_rules.get("required") or BS + "begin{abstract}" in text or not abstract_rules.get("auto_insert"):
        return text
    template = _build_block(abstract_rules.get("template", []))
    new_text = _insert_after_marker(text, abstract_rules.get("insert_after", BS + "maketitle"), template) if template else text
    if new_text != text:
        actions.append(RepairAction("missing_abstract", "Inserted abstract template from rule profile."))
    return new_text


def _ensure_keywords(text: str, keyword_rules: dict, actions: list[RepairAction]) -> str:
    if not keyword_rules.get("required") or not keyword_rules.get("auto_insert"):
        return text
    pattern = "|".join(f"(?:{item})" for item in KEYWORD_PATTERNS)
    if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
        return text
    template = _build_block(keyword_rules.get("template", ""))
    new_text = _insert_after_marker(text, keyword_rules.get("insert_after", "abstract"), template) if template else text
    if new_text != text:
        actions.append(RepairAction("missing_keywords", "Inserted keyword template from rule profile."))
    return new_text


def _normalize_caption_prefixes(text: str, env: str, prefix: str, separator: str, actions: list[RepairAction]) -> str:
    begin_marker, end_marker = f"{BS}begin{{{env}}}", f"{BS}end{{{env}}}"
    caption_marker = BS + "caption{"
    duplicate_start = f"{prefix}{separator}"
    lines, in_env, changed = text.splitlines(), False, False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(begin_marker):
            in_env = True
        elif stripped.startswith(end_marker):
            in_env = False
        elif in_env and stripped.startswith(caption_marker) and stripped.endswith("}"):
            content = stripped[len(caption_marker):-1].strip()
            if content.startswith(duplicate_start):
                indent = line[: len(line) - len(line.lstrip())]
                lines[index] = f"{indent}{caption_marker}{content[len(duplicate_start):].lstrip()}}}"
                changed = True
    if changed:
        actions.append(RepairAction(f"{env}_caption_duplicate_label", f"Removed duplicated {env} caption label."))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def repair(text: str, rules: dict) -> tuple[str, list[RepairAction]]:
    actions: list[RepairAction] = []
    separators = rules.get("caption_separators", {})
    text = _ensure_documentclass(text, rules, actions)
    text = _ensure_abstract(text, rules.get("abstract", {}), actions)
    text = _ensure_keywords(text, rules.get("keywords", {}), actions)
    if not rules.get("class_managed_layout"):
        text = _ensure_geometry(text, rules["geometry"], actions)
    text = _ensure_linespread(text, rules["line_spread"], actions)
    text = _ensure_bibliographystyle(text, rules["bibliographystyle"], actions)
    text = _normalize_caption_prefixes(text, "figure", rules["caption_prefixes"]["figure"], separators.get("figure", ": "), actions)
    text = _normalize_caption_prefixes(text, "table", rules["caption_prefixes"]["table"], separators.get("table", ": "), actions)
    return text, actions
