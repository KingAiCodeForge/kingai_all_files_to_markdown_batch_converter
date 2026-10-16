"""Read-only regressions for converter output planning.

All fixtures are tiny dummy files. No document parsers, executors or external
network services are invoked by these tests.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import convert


class ConversionPathSafetyTests(unittest.TestCase):
    def test_self_alias_rejected_even_with_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "notes.md"
            original = "# Important original source\n"
            source.write_text(original, encoding="utf-8")
            result = convert.convert_single_file((source, source, True, False))
            self.assertFalse(result.success)
            self.assertIn("aliases input", result.error_message)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_self_alias_not_misreported_as_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "notes.md"
            source.write_text("Keep this file\n", encoding="utf-8")
            result = convert.convert_single_file((source, source, False, False))
            self.assertFalse(result.success)
            self.assertNotIn("Skipped", result.error_message)

    def test_collision_blocks_two_sources_without_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "same.pdf").write_bytes(b"%PDF-1.4 dummy")
            (root / "same.docx").write_bytes(b"PK\\x03\\x04dummy")
            output = root / "out"
            result = convert.batch_convert(
                [str(root)], output_dir=str(output),
                extensions=["pdf", "docx"], overwrite=True, max_workers=1,
            )
            self.assertEqual(result.total_files, 2)
            self.assertEqual(result.failed, 2)
            self.assertTrue(any("Output collision" in x for x in result.errors))
            self.assertFalse(output.exists())

    def test_cross_input_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            markdown = root / "same.md"
            original = "# Don't overwrite me\n"
            markdown.write_text(original, encoding="utf-8")
            (root / "same.txt").write_text("Another input\n", encoding="utf-8")
            result = convert.batch_convert(
                [str(root)], extensions=["md", "txt"],
                overwrite=True, max_workers=1,
            )
            self.assertEqual(result.failed, 2)
            self.assertTrue(any("aliases input" in x for x in result.errors))
            self.assertEqual(markdown.read_text(encoding="utf-8"), original)

    def test_dry_run_creates_no_progress_files_or_output_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.pdf").write_bytes(b"%PDF-1.4 dummy")
            output = root / "planned" / "result"
            progress = root / "planned" / "progress.log"
            result = convert.batch_convert(
                [str(root)], output_dir=str(output),
                extensions=["pdf"], dry_run=True, max_workers=1,
                log_file=str(progress),
            )
            self.assertEqual(result.total_files, 1)
            self.assertEqual(result.failed, 0)
            self.assertFalse(output.exists())
            self.assertFalse(progress.exists())
            self.assertFalse((output / ".convert_checkpoint.json").exists())

    def test_short_plain_text_passes_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            for filename in ("tiny.md", "tiny.txt", "tiny.csv"):
                with self.subTest(filename=filename):
                    path = Path(tmp) / filename
                    path.write_text("OK\n", encoding="utf-8")
                    self.assertTrue(convert.validate_file_preflight(path)[0])

    def test_symlinked_destination_does_not_overwrite_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            source.write_text("Source data must survive\n", encoding="utf-8")
            alias = root / "output.md"
            try:
                alias.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            result = convert.convert_single_file((source, alias, True, False))
            self.assertFalse(result.success)
            self.assertEqual(source.read_text(encoding="utf-8"),
                             "Source data must survive\n")


if __name__ == "__main__":
    unittest.main()
