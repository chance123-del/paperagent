from __future__ import annotations

import re
from typing import Iterable

from .models import AnalysisResult, Issue


KEYWORD_PATTERNS = (
    r"\\keywords\{.*?\}",
    r"^\s*keywords?\s*:",
    r"^\s*key\s*words?\s*:",
    r"^\s*关键词\s*[：:]",
)


def _has_keywords(text: str) -> bool:
    pattern = "|".join(f"(?:{item})" for item in KEYWORD_PATTERNS)
    return bool(re.search(pattern, text, re.IGNORECASE | re.MULTILINE))


def _has_abstract(text: str) -> bool:
    return "\\begin{abstract}" in text and "\\end{abstract}" in text


def _has_bibliography(text: str) -> bool:
    return "\\bibliography{" in text or "\\printbibliography" in text


def _has_geometry(text: str, expected: str) -> bool:
    pattern = re.compile(r"\\usepackage(?:\[(?P<opts>[^\]]*)\])?\{geometry\}")
    match = pattern.search(text)
    if not match:
        return False
    opts = match.group("opts") or ""
    return expected.replace(" ", "") == opts.replace(" ", "")


def _has_linespread(text: str, expected: str) -> bool:
    pattern = re.compile(r"\\linespread\{(?P<value>[^}]*)\}")
    match = pattern.search(text)
    if not match:
        return False
    return match.group("value").strip() == expected


def _has_bibliographystyle(text: str, expected: str) -> bool:
    pattern = re.compile(r"\\bibliographystyle\{(?P<style>[^}]*)\}")
    match = pattern.search(text)
    if not match:
        return False
    return match.group("style").strip() == expected


def _caption_issues(lines: Iterable[str], environment: str, prefix: str, separator: str) -> list[Issue]:
    issues: list[Issue] = []
    in_env = False
    duplicated_start = f"{prefix}{separator}"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"\\begin{{{environment}}}"):
            in_env = True
        elif stripped.startswith(f"\\end{{{environment}}}"):
            in_env = False
        elif in_env and stripped.startswith("\\caption{"):
            content = stripped[len("\\caption{"):-1] if stripped.endswith("}") else stripped
            if content.startswith(duplicated_start):
                issues.append(
                    Issue(
                        rule_id=f"{environment}_caption_duplicate_label",
                        severity="medium",
                        message=f"{environment.title()} caption repeats its automatic label '{duplicated_start}'.",
                        auto_fixable=True,
                    )
                )
    return issues


def analyze(text: str, rules: dict) -> AnalysisResult:
    result = AnalysisResult()
    abstract_rules = rules.get("abstract", {})
    keyword_rules = rules.get("keywords", {})
    separators = rules.get("caption_separators", {})

    if abstract_rules.get("required") and not _has_abstract(text):
        result.issues.append(
            Issue(
                "missing_abstract",
                "high",
                "Abstract environment is missing.",
                auto_fixable=bool(abstract_rules.get("auto_insert")),
            )
        )

    if keyword_rules.get("required") and not _has_keywords(text):
        result.issues.append(
            Issue(
                "missing_keywords",
                "medium",
                "Keywords section is missing.",
                auto_fixable=bool(keyword_rules.get("auto_insert")),
            )
        )

    if rules["required_elements"].get("bibliography") and not _has_bibliography(text):
        result.issues.append(
            Issue("missing_bibliography", "high", "Bibliography command is missing.", auto_fixable=False)
        )

    if not rules.get("class_managed_layout") and not _has_geometry(text, rules["geometry"]):
        result.issues.append(
            Issue("geometry_mismatch", "high", "Geometry settings are missing or incorrect.", auto_fixable=True)
        )

    if not _has_linespread(text, rules["line_spread"]):
        result.issues.append(
            Issue("line_spread_mismatch", "medium", "Line spread is missing or incorrect.", auto_fixable=True)
        )

    if not _has_bibliographystyle(text, rules["bibliographystyle"]):
        result.issues.append(
            Issue(
                "bibliographystyle_mismatch",
                "medium",
                "Bibliography style is missing or incorrect.",
                auto_fixable=True,
            )
        )

    lines = text.splitlines()
    result.issues.extend(
        _caption_issues(
            lines,
            "figure",
            rules["caption_prefixes"]["figure"],
            separators.get("figure", ": "),
        )
    )
    result.issues.extend(
        _caption_issues(
            lines,
            "table",
            rules["caption_prefixes"]["table"],
            separators.get("table", ": "),
        )
    )
    return result
