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

    enabled = lambda value: "是" if value else "否"
    lines = [
        f"## 规则包：`{rules.get('name', 'rule_profile')}`",
        "",
        f"说明：{rules.get('description', '未提供说明。')}",
        "",
        "### 当前核验项",
        "",
        f"- 页面尺寸与页边距：`{rules.get('geometry', '未设置')}`",
        f"- 行距倍数：`{rules.get('line_spread', '未设置')}`",
        f"- 参考文献样式：`{rules.get('bibliographystyle', '未设置')}`",
        f"- 是否要求摘要：`{enabled(abstract_rules.get('required'))}`",
        f"- 是否要求关键词：`{enabled(keyword_rules.get('required'))}`",
        f"- 是否要求参考文献：`{enabled(required.get('bibliography'))}`",
    ]

    if caption_rules:
        lines.extend(
            [
                f"- 图注前缀格式：`{caption_rules.get('figure', '未设置')}{separators.get('figure', ': ')}`",
                f"- 表注前缀格式：`{caption_rules.get('table', '未设置')}{separators.get('table', ': ')}`",
            ]
        )

    lines.extend(
        [
            "",
            "### 自动修复策略",
            "",
            f"- 自动补入摘要模板：`{enabled(abstract_rules.get('auto_insert'))}`",
            f"- 自动补入关键词模板：`{enabled(keyword_rules.get('auto_insert'))}`",
            "- 图表注格式与页面样式不一致时，仅执行规则性规范化；不改写正文事实、数据或结论。",
        ]
    )

    if abstract_rules.get("template"):
        lines.extend(["", "### 摘要模板", ""])
        template = abstract_rules["template"]
        if isinstance(template, list):
            lines.extend([f"- `{line}`" for line in template])
        else:
            lines.append(f"- `{template}`")

    if keyword_rules.get("template"):
        lines.extend(["", "### 关键词模板", "", f"- `{keyword_rules['template']}`"])

    return "\n".join(lines)
