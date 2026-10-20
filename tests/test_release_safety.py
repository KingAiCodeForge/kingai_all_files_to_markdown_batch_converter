"""Synthetic offline safety and CLI regressions; no user documents are read."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import convert


class ReleaseSafetyTests(unittest.TestCase):
    def test_output_file_directory_overlap_blocks_entire_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            root.mkdir()
            (root / "same.txt").write_text("first source", encoding="utf-8")
            (root / "same.md").mkdir()
            (root / "same.md" / "nested.txt").write_text("second source", encoding="utf-8")
            output = Path(tmp) / "output"
            result = convert.batch_convert([str(root)], extensions=["txt"], output_dir=str(output))
            self.assertGreater(result.failed, 0)
            self.assertFalse(output.exists())

    def test_worker_error_strings_keep_actionable_categories(self):
        self.assertEqual(convert.classify_error(Exception("Missing dependency: synthetic")), "dependency_missing")
        self.assertEqual(convert.classify_error(Exception("Pre-flight: too small")), "invalid_file")

    def test_hardlink_source_alias_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            destination = Path(tmp) / "different.md"
            source.write_text("preserve original", encoding="utf-8")
            try:
                os.link(source, destination)
            except OSError:
                self.skipTest("hardlinks unavailable")
            result = convert.convert_single_file((source, destination, True, False))
            self.assertFalse(result.success)
            self.assertIn("aliases input", result.error_message)
            self.assertEqual(source.read_text(), "preserve original")

    def test_auxiliary_aliases_refuse_entire_batch_before_mutation(self):
        for option in ("report_file", "log_file"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "source.txt"
                source.write_text("preserve original", encoding="utf-8")
                output = root / "new output"
                result = convert.batch_convert([str(source)], extensions=["txt"],
                    output_dir=str(output), **{option: str(source)})
                self.assertGreater(result.failed, 0)
                self.assertFalse(output.exists())
                self.assertEqual(source.read_text(), "preserve original")

    def test_dry_run_report_alias_does_not_touch_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("preserve original", encoding="utf-8")
            process = subprocess.run([sys.executable, str(Path(convert.__file__).resolve()),
                str(source), "-e", "txt", "--dry-run", "--report", str(source)],
                capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stderr)
            self.assertEqual(source.read_text(), "preserve original")

    def test_report_cannot_replace_markdown_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("source", encoding="utf-8")
            output = Path(tmp) / "out"
            result = convert.batch_convert([str(source)], extensions=["txt"],
                output_dir=str(output), report_file=str(output / "source.md"))
            self.assertGreater(result.failed, 0)
            self.assertFalse(output.exists())

    def test_missing_input_and_invalid_limits_fail_before_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                convert.batch_convert([str(root / "missing")], output_dir=str(root / "out"))
            for arguments in ({"max_workers": 0}, {"batch_size": 0}, {"file_timeout": -1}):
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    convert.batch_convert([str(root)], **arguments)
            self.assertFalse((root / "out").exists())

    def test_failed_atomic_publish_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "existing.md"
            target.write_text("previous complete output", encoding="utf-8")
            with patch.object(convert.os, "replace", side_effect=OSError("injected failure")):
                with self.assertRaises(OSError):
                    convert._write_text_atomic(target, "new output")
            self.assertEqual(target.read_text(), "previous complete output")
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_atomic_no_clobber_preserves_late_arriving_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "existing.md"
            target.write_text("another process", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                convert._write_text_atomic(target, "our output", overwrite=False)
            self.assertEqual(target.read_text(), "another process")

    def test_missing_dependency_does_not_replace_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            target = Path(tmp) / "existing.md"
            source.write_text("source content", encoding="utf-8")
            target.write_text("previous complete output", encoding="utf-8")
            with patch.object(convert, "extract_with_markitdown", side_effect=ImportError("synthetic missing dependency")):
                result = convert.convert_single_file((source, target, True, False))
            self.assertFalse(result.success)
            self.assertIn("Missing dependency", result.error_message)
            self.assertEqual(target.read_text(), "previous complete output")

    def test_overlapping_roots_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            source.write_text("content", encoding="utf-8")
            self.assertEqual(len(convert.find_documents([str(root), str(source)], ["txt"])), 1)

    def test_single_file_output_keeps_source_stem(self):
        source = Path("folder") / "source.txt"
        self.assertEqual(convert.get_output_path(source, Path("out"), base_dir=source), Path("out/source.md"))

    def test_malformed_checkpoint_shape_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint.json"
            for value in ([], {"completed_paths": 1}, {"completed_paths": [42]}):
                checkpoint.write_text(json.dumps(value), encoding="utf-8")
                self.assertEqual(convert.load_checkpoint(checkpoint), {})

    def test_existing_pdf_assets_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "source.md"
            assets = Path(tmp) / "source_assets"
            assets.mkdir()
            original = assets / "page_0001.png"
            original.write_bytes(b"original image bytes")
            # Import availability is intentionally irrelevant to this test.
            with patch.dict(sys.modules, {"fitz": object()}):
                with self.assertRaises(FileExistsError):
                    convert.render_pdf_pages_to_markdown(Path(tmp) / "source.pdf", target)
            self.assertEqual(original.read_bytes(), b"original image bytes")


if __name__ == "__main__":
    unittest.main()
