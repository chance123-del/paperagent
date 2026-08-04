from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from paperformat_agent.formulas import apply_formulas, load_formulas


class FormulaTests(unittest.TestCase):
    def test_inserts_confirmed_formula_and_right_side_tag(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "formulas.json"
            source.write_text(
                json.dumps({"formulas": [{"formula_id": "Eq1", "latex": r"E = mc^2", "tag": "1"}]}),
                encoding="utf-8",
            )
            formulas = load_formulas(str(source), root)
            tex, matched, missing = apply_formulas("\\documentclass{article}\n[Eq1]", formulas)

            self.assertEqual(matched, ["[Eq1] -> eq1"])
            self.assertFalse(missing)
            self.assertIn(r"\usepackage{amsmath}", tex)
            self.assertIn(r"\tag{1}", tex)

    def test_blocks_handwritten_image_without_confirmed_latex(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "formulas.json"
            source.write_text(json.dumps({"formulas": [{"formula_id": "公式1"}]}), encoding="utf-8")
            formulas = load_formulas(str(source), root)
            _, matched, missing = apply_formulas("[公式1]", formulas)

            self.assertFalse(matched)
            self.assertEqual(len(missing), 1)
            self.assertEqual(len(formulas.warnings), 1)

    def test_rejects_unsafe_latex_and_tag(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "upload.json"
            source.write_text(
                json.dumps(
                    {
                        "formulas": [
                            {"formula_id": "Eq1", "latex": r"\input{secret}"},
                            {"formula_id": "Eq2", "latex": "x+y", "tag": r"1}\input{secret}"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            formulas = load_formulas(str(source), root)

            self.assertFalse(formulas.latex)
            self.assertEqual(len(formulas.warnings), 2)


if __name__ == "__main__":
    unittest.main()
