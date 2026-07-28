from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Issue:
    rule_id: str
    severity: str
    message: str
    auto_fixable: bool = False


@dataclass
class RepairAction:
    rule_id: str
    description: str


@dataclass
class AnalysisResult:
    issues: List[Issue] = field(default_factory=list)
    actions: List[RepairAction] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)


@dataclass
class RiskAssessment:
    overall_score: int
    category_scores: Dict[str, int]
    summary: str
