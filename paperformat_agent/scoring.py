from __future__ import annotations

from .models import AnalysisResult, RiskAssessment


SEVERITY_PENALTIES = {
    "high": 18,
    "medium": 10,
    "low": 4,
}

CATEGORY_LABELS = {
    "structure": "Structure",
    "layout": "Layout",
    "captions": "Captions",
    "references": "References",
    "other": "Other",
}


def _issue_category(rule_id: str) -> str:
    if rule_id.startswith("missing_abstract") or rule_id.startswith("missing_keywords"):
        return "structure"
    if rule_id.startswith("geometry") or rule_id.startswith("line_spread"):
        return "layout"
    if "caption" in rule_id:
        return "captions"
    if "bibliography" in rule_id:
        return "references"
    return "other"


def assess_risk(analysis: AnalysisResult) -> RiskAssessment:
    base_scores = {key: 100 for key in CATEGORY_LABELS}
    touched_categories: set[str] = set()

    for issue in analysis.issues:
        category = _issue_category(issue.rule_id)
        touched_categories.add(category)
        penalty = SEVERITY_PENALTIES.get(issue.severity, 6)
        base_scores[category] = max(0, base_scores[category] - penalty)

    if touched_categories:
        used_categories = {category: base_scores[category] for category in touched_categories}
    else:
        used_categories = {
            "structure": 100,
            "layout": 100,
            "captions": 100,
            "references": 100,
        }

    overall_score = round(sum(used_categories.values()) / len(used_categories))

    if overall_score >= 90:
        summary = "Low format risk. The document is close to compliant."
    elif overall_score >= 75:
        summary = "Moderate format risk. A few visible formatting issues remain."
    elif overall_score >= 60:
        summary = "Elevated format risk. Several rules are still violated."
    else:
        summary = "High format risk. The document needs substantial repair before submission."

    labeled_scores = {
        CATEGORY_LABELS[key]: value
        for key, value in used_categories.items()
    }
    return RiskAssessment(overall_score=overall_score, category_scores=labeled_scores, summary=summary)
