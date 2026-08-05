from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from paperformat_agent.asset_manifest import build_asset_manifest, write_asset_manifest
from paperformat_agent.placeholders import apply_placeholder_assets, find_placeholders, scan_assets, unpack_bundle


class AssetDeliveryTests(unittest.TestCase):
    def test_duplicate_assets_are_not_inserted_and_are_manifested(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "Fig1.png").write_bytes(b"one")
            (bundle / "Fig1.jpg").write_bytes(b"two")
            tex, matched, missing, duplicates = apply_placeholder_assets("Before [Fig1] after", bundle, root / "project", {})

            self.assertEqual(tex, "Before [Fig1] after")
            self.assertFalse(matched)
            self.assertFalse(missing)
            self.assertEqual(len(duplicates), 1)
            assets, ignored = scan_assets(bundle)
            records = build_asset_manifest(bundle, find_placeholders(tex), assets, ignored)
            write_asset_manifest(records, root / "project")
            self.assertEqual(records[0]["status"], "duplicate")
            self.assertTrue((root / "project" / "asset_manifest.json").exists())
            self.assertTrue((root / "project" / "asset_manifest.csv").exists())

    def test_asset_archive_rejects_executable_members(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "assets.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("Fig1.png", b"image")
                bundle.writestr("launch.ps1", b"Write-Host unsafe")
            with self.assertRaisesRegex(ValueError, "disallowed executable"):
                unpack_bundle(str(archive), root / "workspace")

    def test_chinese_asset_caption_adds_ctex_support(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "Fig1.png").write_bytes(b"image")
            tex = "\\documentclass{article}\n\\begin{document}\n[Fig1]\n\\end{document}"

            updated, matched, missing, duplicates = apply_placeholder_assets(
                tex,
                bundle,
                root / "project",
                {},
                figure_captions={"fig1": "系统架构"},
            )

            self.assertTrue(matched)
            self.assertFalse(missing)
            self.assertFalse(duplicates)
            self.assertIn(r"\usepackage[UTF8]{ctex}", updated)
            self.assertIn(r"\caption{系统架构}", updated)

    def test_manifest_binds_named_figure_placeholder(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "framework.png").write_bytes(b"image")
            (bundle / "manifest.csv").write_text(
                "id,type,file\nfig-framework,figure,framework.png\n",
                encoding="utf-8",
            )

            updated, matched, missing, duplicates = apply_placeholder_assets(
                "\\documentclass{article}\n\\begin{document}\n[FIG:fig-framework]\n\\end{document}",
                bundle,
                root / "project",
                {},
            )

            self.assertIn(r"\includegraphics", updated)
            self.assertEqual(matched, ["[FIG:fig-framework] -> framework.png"])
            self.assertFalse(missing)
            self.assertFalse(duplicates)


if __name__ == "__main__":
    unittest.main()
