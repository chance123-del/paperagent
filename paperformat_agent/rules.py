from __future__ import annotations

import json
import copy
from pathlib import Path


def default_rules() -> dict:
    return {
        "name": "generic",
        "description": "Neutral formatting rules used when no base profile is selected.",
        "geometry": "a4paper,margin=2.54cm",
        "line_spread": "1.5",
        "bibliographystyle": "plain",
        "abstract": {
            "required": False,
            "auto_insert": False,
            "insert_after": "\\maketitle",
            "template": [],
        },
        "keywords": {
            "required": False,
            "auto_insert": False,
            "insert_after": "abstract",
            "template": "",
        },
        "caption_prefixes": {
            "figure": "Figure",
            "table": "Table",
        },
        "caption_separators": {
            "figure": ": ",
            "table": ": ",
        },
        "required_elements": {
            "bibliography": False,
        },
        "insertion_policy": {
            "figure_width": "0.85\\linewidth",
            "figure_alignment": "\\centering",
            "figure_float": "H",
            "table_alignment": "\\centering",
            "table_float": "H",
            "table_columns": "l",
            "hyperlink_command": "href",
        },
    }


def load_rules(rule_path: str | Path) -> dict:
    path = Path(rule_path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def rules_for_source_kind(rules: dict, source_kind: str) -> dict:
    """Keep author prose unchanged for every supported input format."""
    adapted = copy.deepcopy(rules)
    # Rule templates describe requirements; they must not create manuscript prose.
    # Missing content remains a reported issue for the author to address.
    adapted.get("abstract", {}).update({"auto_insert": False})
    adapted.get("keywords", {}).update({"auto_insert": False})
    return adapted


def summarize_rules(rules: dict) -> str:
    abstract_rules = rules.get("abstract", {})
    keyword_rules = rules.get("keywords", {})
    caption_rules = rules.get("caption_prefixes", {})
    separators = rules.get("caption_separators", {})
    required = rules.get("required_elements", {})

    lines = [
        f"## {rules.get('name', 'rule_profile')}",
        "",
        rules.get("description", "No description."),
        "",
        "### Checks",
        "",
        f"- Geometry: `{rules.get('geometry', 'not set')}`",
        f"- Line spread: `{rules.get('line_spread', 'not set')}`",
        f"- Bibliography style: `{rules.get('bibliographystyle', 'not set')}`",
        f"- Abstract required: `{bool(abstract_rules.get('required'))}`",
        f"- Keywords required: `{bool(keyword_rules.get('required'))}`",
        f"- Bibliography required: `{bool(required.get('bibliography'))}`",
    ]

    if caption_rules:
        lines.extend(
            [
                f"- Figure caption format: `{caption_rules.get('figure', 'not set')}{separators.get('figure', ': ')}`",
                f"- Table caption format: `{caption_rules.get('table', 'not set')}{separators.get('table', ': ')}`",
            ]
        )

    lines.extend(
        [
            "",
            "### Auto Repair",
            "",
            f"- Insert abstract template: `{bool(abstract_rules.get('auto_insert'))}`",
            f"- Insert keyword template: `{bool(keyword_rules.get('auto_insert'))}`",
            "- Normalize caption formats and page style settings when mismatched.",
        ]
    )

    if abstract_rules.get("template"):
        lines.extend(["", "### Abstract Template", ""])
        template = abstract_rules["template"]
        if isinstance(template, list):
            lines.extend([f"- `{line}`" for line in template])
        else:
            lines.append(f"- `{template}`")

    if keyword_rules.get("template"):
        lines.extend(["", "### Keyword Template", "", f"- `{keyword_rules['template']}`"])

    return "\n".join(lines)
