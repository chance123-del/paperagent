from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from paperformat_agent.bibliography import apply_numeric_markers
from paperformat_agent.models import RepairAction


class BibliographyMarkerTests(unittest.TestCase):
    def test_named_cite_markers_resolve_against_bibtex_keys(self) -> None:
        with TemporaryDirectory() as directory:
            bib = Path(directory) / "references.bib"
            bib.write_text("@article{smith2024, title={Example}}\n@book{wang2023, title={Book}}", encoding="utf-8")
            actions: list[RepairAction] = []

            converted, mappings, unresolved = apply_numeric_markers(
                "Prior work [CITE:smith2024; wang2023] and [CITE:missing2020].",
                bib,
                actions,
            )

            self.assertIn(r"\cite{smith2024,wang2023}", converted)
            self.assertIn("[CITE:missing2020]", converted)
            self.assertEqual(mappings, [("CITE:smith2024", "smith2024"), ("CITE:wang2023", "wang2023")])
            self.assertEqual(unresolved, ["missing2020"])
            self.assertTrue(actions)


if __name__ == "__main__":
    unittest.main()
