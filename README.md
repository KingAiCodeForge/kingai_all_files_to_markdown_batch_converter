# KingAI Markdown Converter

Batch-convert document collections into Markdown for human review, full-text
search, and LLM/RAG workflows. The CLI preserves the input folder structure,
records extraction quality, resumes interrupted runs, and isolates files that
stall third-party parsers.

## Features

- Parallel conversion with bounded per-file execution
- Recursive folder scanning and structure-preserving output
- Existing-output skipping, checkpoints, and `--resume`
- Pre-flight file signature and minimum-size validation
- JSON reports with extraction method, timing, word count, and errors
- Four PDF text extractors with automatic best-result selection
- Page-image preservation when a PDF contains no extractable text
- Direct populated-cell extraction for `.xlsx` and `.xls` workbooks
- LibreOffice fallback for legacy Microsoft Office and OpenDocument files
- Recovery of regular RTF files and logger-produced RTF body fragments
- Rich or `tqdm` progress display with a plain-text fallback

## Supported Formats

| Extensions | Handling |
| --- | --- |
| `.pdf` | Best result from MarkItDown, PyMuPDF4LLM, PyMuPDF, and pdfplumber; page images when no text exists |
| `.docx`, `.pptx` | MarkItDown and its installed document backends |
| `.doc`, `.ppt` | MarkItDown with LibreOffice conversion fallback |
| `.xlsx`, `.xls` | Populated-cell Markdown tables, with fallbacks for difficult workbooks |
| `.odt`, `.ods`, `.odp` | MarkItDown with LibreOffice conversion fallback |
| `.rtf` | `striprtf`, including recoverable body fragments, with LibreOffice fallback |
| `.epub`, `.html`, `.htm` | MarkItDown |
| `.csv`, `.json`, `.xml` | MarkItDown |
| `.msg`, `.eml` | MarkItDown |
| `.ipynb`, `.txt`, `.md` | MarkItDown |

Run `python convert.py --list-extensions` for the extension list reported by
the installed version.

## Requirements

- Python 3.10 or newer is recommended.
- Install Python dependencies from `requirements.txt`.
- LibreOffice is optional but strongly recommended for `.doc`, `.xls`, `.ppt`,
  `.odt`, `.ods`, `.odp`, and difficult `.rtf` files.

The converter searches `PATH` and the normal Windows LibreOffice installation
folders. Set `LIBREOFFICE_PATH` to the `soffice` executable when LibreOffice is
installed elsewhere.

```powershell
python -m pip install -r requirements.txt
```

## Basic Usage

The default extensions are `pdf` and `docx`.

```powershell
# Convert PDFs recursively and write Markdown beside each source file.
python convert.py "./documents" --extensions pdf

# Preserve the source tree under a separate output folder.
python convert.py "./documents" -o "./markdown" --extensions pdf doc docx xls xlsx ppt pptx rtf

# Preview work without creating output.
python convert.py "./documents" -o "./markdown" --dry-run --extensions pdf docx xlsx
```

## Large Collections

```powershell
# Resume from the output folder's checkpoint and skip existing Markdown.
python convert.py "./documents" -o "./markdown" --resume --extensions pdf doc docx xls xlsx ppt pptx rtf

# Limit concurrency and stop a parser that spends more than 90 seconds on one file.
python convert.py "./documents" -o "./markdown" --workers 4 --file-timeout 90 --extensions pdf docx xlsx

# Use two workers for memory-heavy or failure-prone collections.
python convert.py "./documents" -o "./markdown" --safe-mode --resume --extensions pdf docx

# Save machine-readable results and a persistent progress log.
python convert.py "./documents" -o "./markdown" --report "./conversion_report.json" --log-file "./conversion.log"
```

The default timeout is 300 seconds per file. Use `--file-timeout 0` only when
an extractor may legitimately run without a bound. Execution waves are capped
to the active worker count so queued files do not consume their timeout before
they begin processing.

## Existing Output and Resume Rules

- Without `--overwrite`, an existing destination `.md` file is skipped.
- `--resume` loads `.convert_checkpoint.json` from the output folder.
- A checkpoint entry is trusted only while its expected Markdown file exists.
- Missing output is retried even when an older checkpoint says it completed.
- `.git`, `.vs`, virtual environments, package caches, and common build folders
  are excluded from recursive scans automatically.

Use `--overwrite` when source content changed and the existing Markdown must be
rebuilt.

## Output

Each Markdown file begins with source, conversion time, extraction method, and
quality-count comments. Folder structure is preserved by default when one input
root is supplied.

For a text-free or image-only PDF, the converter writes:

- A Markdown file with one heading and image link per page
- A sibling `<markdown-name>_assets` folder containing rendered PNG pages

This preserves the document for visual inspection and multimodal tooling. It is
not OCR and does not claim that text inside the images was recognized.

Spreadsheet output includes only populated rows and columns. This avoids huge,
mostly empty Markdown tables caused by stale worksheet dimensions.

## Error Handling

Reports classify failures as `invalid_file`, `corrupt_file`,
`extractor_crash`, `timeout`, `memory_error`, `dependency_missing`, or
`permission_error`. Zero-byte and obviously invalid files fail pre-flight
without being sent through every parser.

When one worker exceeds its timeout, that worker pool is stopped. Other files in
the same execution wave are marked for a later retry instead of being left in an
unknown state. Re-run with `--resume` after correcting the source or increasing
the timeout.

## Limits

- Page-image fallback preserves scanned PDFs but does not perform OCR.
- Extraction cannot guarantee the visual ordering of complex multi-column
  layouts, equations, diagrams, or merged spreadsheet regions.
- Password-protected, damaged, or zero-byte source files require a valid source
  copy before conversion.
- Review safety-critical technical content against the original document.

## Version 1.2

- Added hard per-file timeouts and worker-pool recovery for stalled parsers.
- Fixed timeout accounting so queued files start in worker-sized waves.
- Made checkpoints retry entries whose expected output is missing.
- Added LibreOffice discovery and conversion fallbacks.
- Added `.odt`, `.ods`, and `.odp` support.
- Added populated-cell `.xlsx` and `.xls` extraction.
- Added malformed RTF fragment recovery through `striprtf`.
- Added PDF page-image output when all text extractors return no words.
- Added automatic exclusion of Visual Studio `.vs` folders.

## License

MIT License. See `LICENSE`.

## Author

Jason King, KingAI Pty Ltd

- Email: jason.king@kingai.com.au
- Website: https://kingai.com.au
- Project: https://github.com/KingAiCodeForge/kingai_all_files_to_markdown_batch_converter
