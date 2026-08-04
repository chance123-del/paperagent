import unittest

from paperformat_agent.rules import default_rules, summarize_rules


class RuleSummaryTests(unittest.TestCase):
    def test_summary_is_localized_and_explains_safe_repair_scope(self) -> None:
        summary = summarize_rules(default_rules())

        self.assertIn("当前核验项", summary)
        self.assertIn("自动修复策略", summary)
        self.assertIn("不改写正文事实", summary)
        self.assertNotIn("### Checks", summary)


if __name__ == "__main__":
    unittest.main()
