# Version 1.3 review ledger

The release qualifies bounded output planning, portable CLI installation and
the synthetic document cases listed below. It does not certify every optional
parser, document format, timeout path or extraction's semantic completeness.

| Path / purpose | Inspection and changes | Qualification / remaining limits |
| --- | --- | --- |
| `convert.py`: CLI and discovery | Reviewed all definitions and control flow; no personal machine paths. Validate inputs and numeric bounds; deduplicate overlapping roots; single-file output keeps its stem. | Unit tests, installed CLI help/list/missing-input checks, real synthetic text conversion outside checkout. Windows spaces/non-ASCII paths and Linux checked as recorded in the release receipt. Unsupported extension contents are not validated semantically. |
| `convert.py`: path planner, text writer, reports/logs/checkpoints | Reviewed every write site. Integrates PR #1, checks resolved names, hardlinks, auxiliary output collisions and PDF asset overlap. Atomic text publication; close per-batch file handlers; reject malformed checkpoint shape. | Alias, collision, non-mutating dry-run, late-existing output, atomic failure and dependency failure regressions. Not a defense against hostile concurrent parent-directory replacement. Resume does not hash input content. |
| `convert.py`: preflight and quality metrics | Reviewed signatures/size rules, quality heuristics and error classification. Retains short-text PR fix. | Tiny valid text, malformed/missing paths and synthetic format fixtures tested. Word-count ranking is a heuristic, not semantic correctness. Some generic text formats still use a conservative 50-byte floor. |
| `convert.py`: MarkItDown | Reviewed lazy import and exception handling. | Actual UTF-8 text conversion tested after clean wheel install. Other MarkItDown formats require their optional backends; not all are exercised. |
| `convert.py`: PDF extraction and page images | Reviewed four extractors, best-word-count selection and rendering. Existing asset folders now fail closed. | Synthetic PDF text and blank-page image generation checked with PyMuPDF. Existing assets preserved. PyMuPDF4LLM/layout and pdfplumber full corpus behavior are not qualified. Rendered assets can be partial after interruption; new output location required for retry. |
| `convert.py`: spreadsheet and RTF backends | Reviewed populated-cell extraction, cell escaping, workbook cleanup and RTF recovery. | Actual synthetic sparse XLSX and RTF/body fragments tested when dependencies installed. Legacy XLS and unusual encodings/workbook constructs remain unqualified. |
| `convert.py`: LibreOffice fallback | Reviewed executable/config discovery, isolated temporary profile, timeout and no-shell command construction. Resolve input path for portable subprocess invocation. | No LibreOffice application was installed or launched for release qualification; legacy Office/OpenDocument fallback remains unqualified. |
| `convert.py`: worker waves, adaptive scaling, timeout handling and progress | Reviewed complete process-pool loop, error/report accounting and checkpoint calls. Clamp adaptive scaling to requested worker maximum. | Real single-worker conversion smoke and adaptive-limit unit regression. Stalled third-party parser termination and descendant process cleanup are not qualified. No claim of complete crash-proof conversion. |
| `convert.bat` | Reviewed complete launcher. Uses quoted script path, explicit dependency setup, Python 3.10 guidance, no pause; forwards exit status. | Invoked from an external working directory for help and missing-input errors. Windows only. |
| `tests/test_conversion_path_safety.py` | Reviewed all seven public PR tests; unchanged synthetic-only source. | Included in regression suite. Symlink/hardlink availability is reported by each platform. |
| `tests/test_release_safety.py`, `tests/test_extractors.py` | Newly authored synthetic regressions and actual optional-backend checks. | Included in regression suite. Missing optional-backend tests skip explicitly. |
| `pyproject.toml`, `MANIFEST.in`, `requirements.txt` | Reviewed install entrypoint, Python floor and package contents. Added minimal wheel and source distribution; no dependencies bundled. Existing requirements preserved for optional backends. | Clean local-wheel install, dependency metadata/check, outside-checkout invocation and archive allowlist inspection. No package registry publication. Dependency license terms apply separately. |
| `.gitignore` and local exclude | Reviewed tracked list, repository ignore patterns and local excludes. Removed broad test-source ignores only; keep generated output, credentials and local config excluded. | Explicit public source/test/doc allowlist added normally, without force-add or global-ignore changes. Tracked source/artifacts scanned before release. |
| `kingai_image_to_markdown_ocr_pytesseract_comparer_tool/readme.md` | Inspected: roadmap placeholder only; no executable script exists in this path. | Preserved unchanged in source archive. Not an OCR engine or supported entrypoint. |

No script paths remain uninspected in this public checkout. The unsupported
backend and platform items above remain explicit qualification limits. No
private repository, user document or ignored local variant was available in or
imported into this isolated checkout. Original source remains reachable using
the source-labelled refs in `SOURCES.json` and the original public branch.

Reproduce source tests with `python -m unittest discover -s tests -v`.
Build with `python -m build`; install the wheel into a fresh virtual environment
with `python -m pip install <wheel>`. For actual optional-backend fixtures add
`pymupdf openpyxl striprtf`. Run `python -m pip check` and invoke
`kingai-markdown --help` from a directory outside the checkout. Exact versions,
test outcomes, source SHA and artifact SHA-256 values accompany the release.
