from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from paperformat_agent.source_converter import SourceDocument, _caption_kind_and_text, load_markdown, pdf_table_extraction_warnings, render_latex


class SourceCaptionTests(unittest.TestCase):
    def test_recognises_numbered_chinese_figure_and_table_captions(self) -> None:
        self.assertEqual(_caption_kind_and_text("图 1-2：系统框架"), ("figure", "系统框架"))
        self.assertEqual(_caption_kind_and_text("表2.1 数据集统计"), ("table", "数据集统计"))

    def test_preserves_chinese_markdown_captions_without_body_duplicates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "figure.png").write_bytes(b"image")
            manuscript = root / "paper.md"
            manuscript.write_text(
                "# 测试论文\n\n![图](figure.png)\n图 1：系统框架\n\n"
                "表 1-1：数据集统计\n| 名称 | 数量 |\n| --- | --- |\n| A | 1 |\n",
                encoding="utf-8",
            )
            document = load_markdown(manuscript, root / "assets")
            latex = render_latex(document, {})

            self.assertEqual(document.figure_captions, ["系统框架"])
            self.assertEqual(document.table_captions, ["数据集统计"])
            self.assertIn(r"\caption{系统框架}", latex)
            self.assertIn(r"\caption{数据集统计}", latex)
            self.assertNotIn("图 1：系统框架", latex)

    def test_formula_crop_does_not_consume_a_figure_caption(self) -> None:
        document = SourceDocument(
            title="Formula fidelity",
            blocks=[("figure", Path("page-1-formula-1.png")), ("figure", Path("page-1-image-1.png"))],
            images=[],
            figure_captions=["Actual experiment"],
        )
        latex = render_latex(document, {})

        self.assertEqual(latex.count(r"\begin{figure}"), 1)
        self.assertIn(r"\caption{Actual experiment}", latex)
        self.assertNotIn(r"\caption{Preserved formula}", latex)
        self.assertIn(r"\begin{center}", latex)

    def test_warns_when_pdf_table_structure_is_flattened(self) -> None:
        warnings = pdf_table_extraction_warnings("Table 2 Subjects Age Males Females Training")

        self.assertEqual(len(warnings), 1)
        self.assertIn("行列结构无法可靠还原", warnings[0])


if __name__ == "__main__":
    unittest.main()
