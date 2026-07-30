from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document

from paperformat_agent.exporter import _fallback_docx
from paperformat_agent.rules import rules_for_source_kind


class ExportAndRuleTests(unittest.TestCase):
    def test_docx_fallback_includes_numbered_references(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tex = root / "main.tex"
            tex.write_text(
                "\\begin{document}\n"
                "A cited claim\\cite{one}.\n"
                "\\bibliography{references}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            tex.with_suffix(".bbl").write_text(
                "\\begin{thebibliography}{1}\n"
                "\\bibitem{one} Example Author. Example Title. 2026.\n"
                "\\end{thebibliography}\n",
                encoding="utf-8",
            )
            output = root / "output.docx"

            _fallback_docx(tex, output, root)

            paragraphs = [paragraph.text for paragraph in Document(output).paragraphs]
            self.assertIn("A cited claim[1].", paragraphs)
            self.assertEqual(paragraphs[-2:], ["References", "[1] Example Author. Example Title. 2026."])

    def test_rules_never_insert_author_prose(self) -> None:
        rules = {"abstract": {"auto_insert": True}, "keywords": {"auto_insert": True}}

        adapted = rules_for_source_kind(rules, "tex")

        self.assertFalse(adapted["abstract"]["auto_insert"])
        self.assertFalse(adapted["keywords"]["auto_insert"])
        self.assertTrue(rules["abstract"]["auto_insert"])


if __name__ == "__main__":
    unittest.main()
