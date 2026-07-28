from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import AnalysisResult, RepairAction, RiskAssessment


def build_report(
    analysis: AnalysisResult,
    actions: list[RepairAction],
    rule_name: str,
    risk: RiskAssessment,
    compile_status: str | None = None,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# PaperFormat Agent Report",
        "",
        f"- Rule profile: `{rule_name}`",
        f"- Generated at: `{timestamp}`",
        f"- Total issues: `{len(analysis.issues)}`",
        f"- Auto repairs applied: `{len(actions)}`",
        f"- Format compliance score: `{risk.overall_score}/100`",
    ]

    if compile_status is not None:
        lines.append(f"- Compile verification: `{compile_status}`")

    lines.extend(["", "## Risk Summary", "", f"- {risk.summary}"])
    for label, value in risk.category_scores.items():
        lines.append(f"- {label}: `{value}/100`")

    lines.extend(["", "## Issues", ""])

    if analysis.issues:
        for issue in analysis.issues:
            lines.append(
                f"- [{issue.severity}] `{issue.rule_id}`: {issue.message} Auto-fixable: `{issue.auto_fixable}`"
            )
    else:
        lines.append("- No issues found.")

    lines.extend(["", "## Applied Repairs", ""])
    if actions:
        for action in actions:
            lines.append(f"- `{action.rule_id}`: {action.description}")
    else:
        lines.append("- No automatic repairs were applied.")

    return "\n".join(lines) + "\n"


def write_report(report_text: str, report_path: str | Path) -> None:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text, encoding="utf-8")
