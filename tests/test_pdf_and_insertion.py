from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from paperformat_agent.exporter import export_pdf_from_tex
from paperformat_agent.guidelines import apply_requirement_text
from paperformat_agent.hybrid_insert import build_block, insert_block
from paperformat_agent.journal_resolver import resolve_journal
from paperformat_agent.source_converter import load_pdf
from paperformat_agent.verifier import compile_tex


MINIMAL_TEX = r"""
\documentclass{article}
\usepackage{hyperref}
\begin{document}
\title{离线 PDF 功能验收}
\maketitle
\section{结果}
本地渲染需要保留中文内容和引用链接。
\begin{equation}
E = mc^2
\end{equation}
\href{https://example.org/source}{查看数据来源}
\end{document}
"""


class PdfAndInsertionTests(unittest.TestCase):
    def test_formula_and_hyperlink_blocks_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            formula = build_block("Formula", r"E = mc^2", None, "", project)
            self.assertIn(r"\begin{equation}", formula)
            self.assertIn("E = mc^2", formula)
            with self.assertRaises(ValueError):
                build_block("Formula", r"\input{secrets}", None, "", project)

            link = build_block("Hyperlink", "数据 & 代码", "https://example.org/data", "", project)
            self.assertEqual(link, r"\href{https://example.org/data}{数据 \& 代码}")
            with self.assertRaises(ValueError):
                build_block("Hyperlink", "危险链接", "javascript:alert(1)", "", project)

    def test_insertion_targets_section_or_anchor(self) -> None:
        source = "\\section{结果}\n原始内容\n\\end{document}"
        updated = insert_block(source, "\\begin{equation}x=1\\end{equation}", "结果", "Section start", "")
        self.assertLess(updated.index("x=1"), updated.index("原始内容"))
        anchored = insert_block(source, r"\href{https://x.test}{来源}", "", "After anchor", "原始内容")
        self.assertGreater(anchored.index(r"\href"), anchored.index("原始内容"))

    def test_local_pdf_renderer_preserves_chinese_and_formula(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tex = root / "main.tex"
            pdf = root / "main.pdf"
            tex.write_text(MINIMAL_TEX, encoding="utf-8")
            ok, note = export_pdf_from_tex(tex, pdf, root)
            self.assertTrue(ok, note)
            self.assertGreater(pdf.stat().st_size, 1000)
            extracted = "".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
            self.assertIn("离线 PDF 功能验收", extracted)
            self.assertIn("E = mc^2", extracted)

    def test_compile_falls_back_without_latex_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tex = root / "main.tex"
            tex.write_text(MINIMAL_TEX, encoding="utf-8")
            with patch("paperformat_agent.verifier.resolve_xelatex_binary", return_value=None), patch(
                "paperformat_agent.verifier.resolve_tectonic_binary", return_value=None
            ):
                ok, log = compile_tex(tex, root)
            self.assertTrue(ok)
            self.assertIn("Built-in PDF fallback:", log)
            self.assertTrue((root / "main.pdf").exists())

    def test_tectonic_network_circuit_breaker_skips_repeated_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tex = root / "main.tex"
            tex.write_text(MINIMAL_TEX, encoding="utf-8")
            with patch("paperformat_agent.verifier._TECTONIC_NETWORK_UNAVAILABLE", True), patch(
                "paperformat_agent.verifier.resolve_xelatex_binary", return_value=None
            ), patch("paperformat_agent.verifier.resolve_tectonic_binary", return_value="tectonic.exe"), patch(
                "paperformat_agent.verifier._run_command", side_effect=AssertionError("native compiler should be skipped")
            ):
                ok, log = compile_tex(tex, root)
            self.assertTrue(ok)
            self.assertIn("Tectonic skipped", log)

    def test_generated_pdf_can_be_imported_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tex = root / "main.tex"
            pdf = root / "main.pdf"
            tex.write_text(MINIMAL_TEX, encoding="utf-8")
            self.assertTrue(export_pdf_from_tex(tex, pdf, root)[0])
            loaded = load_pdf(pdf, root / "assets")
            self.assertTrue(loaded.blocks)
            combined = " ".join(str(payload) for kind, payload in loaded.blocks if kind == "paragraph")
            self.assertIn("本地渲染", combined)

    def test_journal_matching_has_offline_result(self) -> None:
        with patch("paperformat_agent.journal_resolver._crossref_lookup", return_value=None):
            match = resolve_journal("Unknown Offline Journal")
        self.assertEqual(match.profile_id, "generic")
        self.assertIn("network unavailable", match.source)

    def test_empty_ui_values_are_accepted(self) -> None:
        rules, changes = apply_requirement_text({"name": "base"}, None)
        self.assertEqual(rules["name"], "base")
        self.assertEqual(changes, [])
        self.assertEqual(resolve_journal(None).profile_id, "generic")


if __name__ == "__main__":
    unittest.main()
