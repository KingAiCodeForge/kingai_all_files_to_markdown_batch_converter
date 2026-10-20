"""Real optional-backend checks using documents created only in a temp folder."""
from pathlib import Path
import tempfile
import unittest
import importlib.util
import convert


class ExtractorTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("markitdown"), "optional backend missing")
    def test_real_text_conversion_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory(prefix="documents ü ") as tmp:
            source = Path(tmp) / "notes 日本語.txt"
            source.write_text("Hello from a portable synthetic source.\n", encoding="utf-8")
            target = Path(tmp) / "output folder" / "notes.md"
            result = convert.convert_single_file((source, target, False, False))
            self.assertTrue(result.success, result.error_message)
            self.assertIn("portable synthetic source", target.read_text(encoding="utf-8"))
            self.assertIn("portable synthetic source", source.read_text(encoding="utf-8"))

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "optional backend missing")
    def test_real_sparse_xlsx(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sparse.xlsx"
            workbook = Workbook()
            workbook.active["B2"] = "left|right"
            workbook.active["D4"] = "retained"
            workbook.save(source)
            workbook.close()
            text, method = convert.extract_spreadsheet_to_markdown(source)
            self.assertEqual(method, "openpyxl-populated-cells")
            self.assertIn("left\\|right", text)
            self.assertIn("retained", text)
            self.assertLess(len(text), 500)

    @unittest.skipUnless(importlib.util.find_spec("striprtf"), "optional backend missing")
    def test_real_rtf_and_body_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.rtf"
            for text in (r"{\rtf1\ansi Synthetic RTF text.\par}", r"Synthetic fragment text.\par}"):
                source.write_text(text, encoding="latin-1")
                result, method = convert.extract_rtf_to_markdown(source)
                self.assertIn("Synthetic", result)
                self.assertIn("striprtf", method)

    @unittest.skipUnless(importlib.util.find_spec("pymupdf"), "optional backend missing")
    def test_real_pdf_text_and_blank_page_rendering(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_pdf = root / "text.pdf"
            with pymupdf.open() as document:
                document.new_page().insert_text((72, 72), "Synthetic PDF text for testing.")
                document.save(text_pdf)
            text, method = convert.extract_with_pymupdf(text_pdf)
            self.assertIn("Synthetic PDF", text)
            self.assertEqual(method, "pymupdf")
            blank_pdf = root / "blank.pdf"
            with pymupdf.open() as document:
                document.new_page()
                document.save(blank_pdf)
            markdown, method = convert.render_pdf_pages_to_markdown(blank_pdf, root / "blank.md")
            self.assertEqual(method, "pymupdf-page-images")
            self.assertIn("blank_assets/page_0001.png", markdown)
            self.assertTrue((root / "blank_assets/page_0001.png").is_file())

    def test_adaptive_workers_never_exceed_requested_limit(self):
        pool = convert.AdaptiveWorkerPool(1)
        for _ in range(3):
            pool.record_result(False)
            pool.record_result(True)
            self.assertLessEqual(pool.get_adjusted_workers(), 1)


if __name__ == "__main__":
    unittest.main()
