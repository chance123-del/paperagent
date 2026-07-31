import unittest

from paperformat_agent.rules import default_rules, summarize_rules


class RuleSummaryTests(unittest.TestCase):
    def test_rule_summary_is_localized_for_the_user_interface(self) -> None:
        summary = summarize_rules(default_rules())

        self.assertIn("当前核验项", summary)
        self.assertIn("是否要求摘要：`否`", summary)
        self.assertIn("自动修复策略", summary)
        self.assertNotIn("### Checks", summary)


if __name__ == "__main__":
    unittest.main()
