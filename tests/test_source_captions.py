from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from paperformat_agent.source_converter import _caption_kind_and_text, load_markdown, pdf_table_extraction_warnings, render_latex


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

    def test_warns_when_a_pdf_table_has_lost_its_structure(self) -> None:
        warning = pdf_table_extraction_warnings("T a b l e 1 Clinical data Subjects Age (years) Training set")

        self.assertEqual(len(warning), 1)
        self.assertIn("could not be reconstructed", warning[0])


if __name__ == "__main__":
    unittest.main()
