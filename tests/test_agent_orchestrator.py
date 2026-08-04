from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from paperformat_agent.agent_orchestrator import PaperDeliveryAgent


class AgentOrchestratorTests(unittest.TestCase):
    def test_blocked_delivery_is_persisted_with_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            agent = PaperDeliveryAgent(Path(directory), "draft.docx", "Test Journal")
            agent.complete_step("intake", "Parsed Word source.", ["project/main.tex"])
            agent.finish(["Missing Fig1"], "成功")

            restored = PaperDeliveryAgent.load(Path(directory))
            self.assertIsNotNone(restored)
            self.assertEqual(restored.trace["status"], "needs_confirmation")
            self.assertEqual(restored.trace["steps"][-1]["status"], "blocked")
            self.assertIn("Missing Fig1", restored.trace["steps"][-1]["evidence"])

    def test_successful_delivery_requires_human_review_first(self) -> None:
        with TemporaryDirectory() as directory:
            agent = PaperDeliveryAgent(Path(directory), "draft.tex", "")
            agent.finish([], "成功")

            self.assertEqual(agent.trace["status"], "ready_for_review")
            self.assertIn("等待审阅", agent.to_markdown())

    def test_formal_export_updates_persisted_status(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            agent = PaperDeliveryAgent(root, "draft.tex", "Journal")
            agent.finish([], "成功")
            agent.mark_formal_export(True, ["PDF: success", "DOCX: success"])

            restored = PaperDeliveryAgent.load(root)
            self.assertEqual(restored.trace["status"], "delivered")
            self.assertTrue(restored.trace["formal_export"]["success"])


if __name__ == "__main__":
    unittest.main()
