# KingAI Markdown Converter

Batch document-to-Markdown converter using Microsoft's MarkItDown with multi-library fallbacks.
Designed for converting large document collections into text that's searchable and usable with LLMs.

Uses multiprocessing — set `--workers` to match your CPU thread count.

## Features

- **Multiprocessing** — parallel conversion across CPU cores
- **Multiple formats** — PDF, DOCX, XLSX, PPTX, HTML, CSV, JSON, and more
- **Multi-library fallback** — 4 PDF extraction backends, picks the best result by word count
- **Pre-flight validation** — checks file headers (magic bytes) and minimum sizes before wasting time on corrupt files
- **Exponential backoff** — jittered delays between extractor failures to reduce thrashing
- **Batch chunking** — processes files in batches of 200 with checkpoints between them
- **Crash recovery** — `--resume` flag to continue from where the last run stopped
- **Adaptive workers** — automatically reduces worker count when failure rate is high or memory is low
- **File logging** — progress log written to disk, survives terminal crashes
- **Error classification** — failures categorized as invalid_file, corrupt, timeout, etc. for actionable reports
- **Recursive scanning** — process entire folder trees
- **Structure preservation** — maintains folder hierarchy in output
- **Dry run mode** — preview what will be converted
- **JSON reports** — detailed conversion statistics

> Complex images, diagrams, and heavily-formatted tables may not convert perfectly. Review output for these.

## Searchability & Context

Converted Markdown is designed for easy string search with technical context:
- Hex addresses like `0x0000`, `$181E1`, `$1823F`
- Protocol keywords like `ALDL`, `OBD-II`, `VPW`, `KWP2000`
- ECU terms like `XDF`, `ADX`, `BIN`, `CAL`, `HC11`
- Headings, tables, and lists preserved for contextual matching
## Supported Formats

| Extension | Type |
|-----------|------|
| `.pdf` | PDF Document |
| `.docx` | Word Document |
| `.doc` | Legacy Word Document |
| `.xlsx` | Excel Spreadsheet |
| `.xls` | Legacy Excel |
| `.pptx` | PowerPoint Presentation |
| `.epub` | E-Book |
| `.html` | HTML Document |
| `.csv` | CSV Data |
| `.json` | JSON Data |
| `.msg` | Outlook Message |
| `.ipynb` | Jupyter Notebook |

## Installation

```powershell
# Install dependencies
pip install -r requirements.txt

# Or install just markitdown with all extras
pip install markitdown[all]
```

## Usage

### Basic Usage

```powershell
# Convert all PDFs in a folder
python convert.py "./documents" --extensions pdf

# Convert PDFs and Word docs
python convert.py "./source" --extensions pdf docx

# Convert to a specific output folder
python convert.py "./documents" -o "./converted_docs"
```

### Advanced Usage

```powershell
# Use maximum workers (adjust for your CPU)
python convert.py "./documents" --workers 16 --extensions pdf docx xlsx

# Dry run to preview
python convert.py "./source" --dry-run

# Overwrite existing conversions
python convert.py "./documents" --overwrite

# Non-recursive (current folder only)
python convert.py "./documents" --no-recursive

# Exclude certain patterns
python convert.py "./source" --exclude __pycache__ .git node_modules

# Flat output (no subdirectories)
python convert.py "./documents" -o "./converted" --flat

# Save conversion report
python convert.py "./documents" --report conversion_report.json -v

# Resume after crash or interruption (v1.1)
python convert.py "./documents" -o "./output" --resume

# Safe mode: 2 workers, maximum caution (v1.1)
python convert.py "./documents" --safe-mode -e pdf

# Custom batch size for very large runs (v1.1)
python convert.py "./documents" --batch-size 100 -e pdf

# Log to file so progress survives terminal crash (v1.1)
python convert.py "./documents" --log-file conversion.log -e pdf
```

### Using the Batch File

```powershell
# Windows - just double-click or run:
convert.bat "./documents" -o "./markdown_exports"
```

## Usage Examples

### Convert PDFs to Markdown
```powershell
python convert.py "./documents" -o "./markdown_exports" --extensions pdf docx -v
```

### Convert source documents
```powershell
python convert.py "./source" -o "./markdown_exports" --extensions pdf docx xlsx pptx -v --exclude __pycache__ .git
```

### Full document export (multiple source dirs)
```powershell
python convert.py "./documents" "./source" -o "./markdown_exports" --workers 12 --extensions pdf docx --report export_report.json -v
```

## Performance

On an i9-9900K with 16 threads:

| Files | Estimated Time |
|-------|---------------|
| 100 PDFs | ~2-5 minutes |
| 500 PDFs | ~10-20 minutes |
| 1000 PDFs | ~25-45 minutes |

Times vary based on PDF complexity, page count, and whether they contain tables/images.

## Error Handling

The converter uses multiple layers of error handling (v1.1):

**Pre-flight validation:**
- Files checked for minimum size and correct magic bytes (file header) before any extraction attempt
- Catches corrupt downloads, zero-byte files, and renamed files immediately

**Extractor fallback with backoff:**
1. **PDF**: markitdown → pymupdf4llm → pymupdf → pdfplumber (with jittered exponential backoff between failures)
2. **DOCX**: mammoth (HTML) → python-docx → basic extraction
3. **XLSX**: openpyxl + pandas → xlrd fallback

**Error classification:**
Each failure is categorized (invalid_file, corrupt_file, extractor_crash, timeout, memory_error, dependency_missing, permission_error) so the JSON report tells you what to fix.

**Crash recovery:**
If the process is interrupted (Ctrl+C, terminal crash, OOM), use `--resume` to continue from the last checkpoint. Progress is saved after each batch.

## Output Format

Each converted file includes:
- Source file comment header
- Timestamp of conversion
- Preserved document structure (headings, tables, lists)

Example output:
```markdown
<!-- Converted from: Service Report.pdf -->
<!-- Converted at: 2026-01-12T15:30:00 -->



## License

MIT - KingAI Pty Ltd

## Author

Jason King
- Email: jason.king@kingai.com.au
- Website: https://kingai.com.au

---

## v1.1 Changes (February 2026)

### Graceful Degradation for Large Runs

When processing thousands of files, v1.0 could crash on corrupt files, run out of memory with too many workers, and lose all progress if the terminal died. v1.1 fixes these specific problems:

**Pre-flight validation:** Checks magic bytes and minimum file sizes before passing files to extractors. A 4-byte "PDF" is caught immediately instead of crashing all 4 extraction backends.

**Exponential backoff with Full Jitter:** When an extractor fails, waits a randomized delay before trying the next one. Based on the AWS architecture pattern (`sleep = random(0, min(cap, base * 2^attempt))`). Prevents correlated resource spikes when multiple workers hit bad files simultaneously.

**Batch chunking:** Files are processed in batches of 200 (configurable with `--batch-size`). Between batches: checkpoint is saved, garbage collection runs, and memory is checked. This prevents submitting 11,000 futures at once.

**Crash recovery:** A `.convert_checkpoint.json` file is saved after each batch. Use `--resume` to skip already-completed files after a crash or interruption.

**Adaptive worker scaling:** Monitors failure rate over a sliding window. If >15% of recent files fail, workers are automatically reduced. If `psutil` is installed, also checks memory pressure and forces safe mode above 90% usage.

**Error classification:** Failures are categorized (invalid_file, corrupt_file, timeout, memory_error, etc.) so the JSON report shows what actually went wrong, not just a wall of tracebacks.

**File logging:** Progress is written to `conversion_progress.log` in the output directory. Survives terminal crashes.

### Multi-Library PDF Extraction with Quality Scoring

The converter now uses **5 different PDF extraction engines** and automatically selects the best result:

| Engine | Strengths |
|--------|-----------|
| `pymupdf4llm` | Best for LLM/RAG, preserves structure, tables |
| `markitdown` | Microsoft's library, good all-rounder |
| `pdfplumber` | Excellent for scanned PDFs and tables |
| `pymupdf` | Fast, good for simple text extraction |
| `pdfminer` | Handles complex layouts well |

**How it works:**
1. Tries pymupdf4llm first (best quality for AI/LLM use)
2. Falls back through other engines if extraction fails
3. Calculates quality metrics (word count, tables, headings)
4. Returns the best extraction automatically

### Live Progress Bar with Percentage

```
Converting:  40%|████████████                  | 2/5 [00:04<00:06,  2.12s/file]
[1/5] ✅ document.pdf
   → 1,234 words | 3.2s | pymupdf4llm
Converting: 100%|██████████████████████████████| 5/5 [00:11<00:00,  2.38s/file]
```

Shows:
- Visual progress bar with percentage
- File count and ETA
- Processing speed (files/sec)
- Real-time file-by-file results with word counts and extraction method used

### Quality Metrics

Each conversion now tracks:
- **Word count** - Total words extracted
- **Character count** - Total characters
- **Table count** - Number of tables detected
- **Heading count** - Number of headings found
- **Extraction method** - Which engine produced the best result

### JSON Report with Full Statistics

```powershell
python convert.py "./documents" --report conversion_report.json
```

Generates detailed JSON report:
```json
{
  "summary": {
    "total_files": 160,
    "successful": 159,
    "failed": 1,
    "total_words_extracted": 450000,
    "extraction_methods": {
      "pymupdf4llm": 85,
      "markitdown": 62,
      "pdfplumber": 12
    }
  },
  "results": [...]
}
```

### Real-World Performance (Tested)

Conversion of **160 files** (including massive 88k-word PDFs):

| Metric | Result |
|--------|--------|
| Total files | 160 |
| Successful | 159 (99.4%) |
| Failed | 1 (corrupt .docx) |
| Total time | 13.6 minutes |
| Avg per file | 5.1 seconds |
| Total words | 500,000+ |

The converter handled files ranging from:
- Small 100-word PDFs (0.1s each)
- Large 88,000-word PDFs (12+ minutes each)
- Scanned PDFs with tables
- Complex multi-column layouts

### Graceful Fallback

If `tqdm` is not installed, falls back to simple percentage display:
```
[45.2%] [72/160] ✅ document.pdf
   → 1,234 words | 3.2s | markitdown
```

---

## Possible Future Work

- **OCR for scanned PDFs** — Tesseract/Pillow integration for image-based text extraction
- **Image-to-text** — Extract text from diagrams, datasheets, and schematics
- **Spreadsheet improvements** — Better table formatting for complex Excel files
- **Vector DB export** — Direct output to Pinecone/ChromaDB for RAG pipelines
- **Keyword tagging** — Auto-tag with domain terms (for automotive: ALDL, OBD-II, KWP2000, etc.)

---

**Project homepage**: https://github.com/KingAiCodeForge/kingai_all_files_to_markdown_batch_converter
