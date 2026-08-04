from __future__ import annotations

import unittest

from paperformat_agent.llm_rule_extractor import (
    analysis_to_rows,
    apply_selected_rule_rows,
    validate_analysis,
)
from paperformat_agent.rules import default_rules


class LlmRuleExtractorTests(unittest.TestCase):
    def test_official_explicit_high_confidence_rule_is_preselected(self) -> None:
        analysis = validate_analysis(
            {
                "document_type": "official_guideline",
                "classification_confidence": 0.96,
                "candidate_rules": [
                    {
                        "rule_key": "line_spread",
                        "value": "1.5",
                        "basis": "explicit",
                        "applicability": "mandatory",
                        "confidence": 0.93,
                        "evidence_quote": "正文必须采用 1.5 倍行距。",
                        "evidence_location": "[PAGE 3]",
                    }
                ],
            }
        )
        rows = analysis_to_rows(analysis)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][0])

    def test_sample_article_rules_require_manual_selection(self) -> None:
        analysis = validate_analysis(
            {
                "document_type": "sample_article",
                "classification_confidence": 0.91,
                "candidate_rules": [
                    {
                        "rule_key": "citation_style",
                        "value": "numeric",
                        "basis": "explicit",
                        "applicability": "mandatory",
                        "confidence": 0.95,
                        "evidence_quote": "[1] A. Author, Example.",
                        "evidence_location": "[PAGE 8]",
                    }
                ],
            }
        )
        candidate = analysis["candidate_rules"][0]
        self.assertEqual(candidate["basis"], "observed")
        self.assertEqual(candidate["applicability"], "candidate")
        self.assertFalse(analysis_to_rows(analysis)[0][0])

    def test_unsafe_or_unsupported_values_are_dropped(self) -> None:
        analysis = validate_analysis(
            {
                "document_type": "official_guideline",
                "candidate_rules": [
                    {
                        "rule_key": "geometry",
                        "value": "a4paper,margin=2.5cm\\input{evil}",
                        "basis": "explicit",
                        "applicability": "mandatory",
                        "confidence": 0.99,
                        "evidence_quote": "malicious",
                        "evidence_location": "[PAGE 1]",
                    },
                    {
                        "rule_key": "author.name",
                        "value": "Alice",
                        "basis": "explicit",
                        "applicability": "mandatory",
                        "confidence": 0.99,
                        "evidence_quote": "Alice",
                        "evidence_location": "[PAGE 1]",
                    },
                ],
            }
        )
        self.assertEqual(analysis["candidate_rules"], [])
        self.assertTrue(analysis["warnings"])

    def test_only_checked_allowlisted_rows_modify_rules(self) -> None:
        rows = [
            [True, "行距", "line_spread", "1.25", "explicit", 0.9, "[PAGE 2] 1.25 倍"],
            [False, "引用", "citation_style", "numeric", "observed", 0.7, "[PAGE 5] [1]"],
            [True, "未知", "unknown.key", "value", "explicit", 1.0, "[PAGE 1] value"],
        ]
        rules, changes = apply_selected_rule_rows(default_rules(), rows)
        self.assertEqual(rules["line_spread"], "1.25")
        self.assertNotIn("citation_style", rules)
        self.assertEqual(len(changes), 1)


if __name__ == "__main__":
    unittest.main()
