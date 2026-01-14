# KingAI Markdown Converter

High-performance batch document-to-Markdown converter powered by Microsoft's MarkItDown with quality-scored multi-library fallbacks. Designed for creating LLM-friendly Markdown from large document collections.

**Optimized for i9-9900K (8 cores / 16 threads)**
please change the workers to your cpu cores / threads if not using i9-9900k. use speccy or hwinfo to find this or info scripts in terminal or powershell.
## Features

- ✅ **Multiprocessing** - Uses all CPU cores for parallel conversion
- ✅ **Multiple formats** - PDF, DOCX, XLSX, PPTX, HTML, CSV, JSON, and more
- ✅ **Smart fallbacks** - Multiple extraction methods per format
- ✅ **Recursive scanning** - Process entire folder trees
- ✅ **Structure preservation** - Maintains folder hierarchy in output
- ✅ **Dry run mode** - Preview what will be converted
- ✅ **JSON reports** - Detailed conversion statistics

> For diagrams and complex images, review output and extract manually if needed.

## Searchability & Context

Converted Markdown is designed for easy string search with technical context:
- Hex addresses like `0x0000`, `$181E1`, `$1823F`
- Protocol keywords like `ALDL`, `OBD-II`, `VPW`, `KWP2000`
- ECU terms like `XDF`, `ADX`, `BIN`, `CAL`, `HC11`
- Headings, tables, and lists preserved for contextual matching
## Supported Formats right now. will expand and add spreadsheets later. image to text next.

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
python convert.py "E:\Downloads" --extensions pdf

# Convert PDFs and Word docs
python convert.py "A:\repos" --extensions pdf docx

# Convert to a specific output folder
python convert.py "E:\Downloads" -o "A:\repos\converted_docs"
```

### Advanced Usage

```powershell
# Use maximum workers (16 for i9-9900K)
python convert.py "E:\Downloads" --workers 16 --extensions pdf docx xlsx

# Dry run to preview
python convert.py "A:\repos" --dry-run

# Overwrite existing conversions
python convert.py "E:\Downloads" --overwrite

# Non-recursive (current folder only)
python convert.py "E:\Downloads" --no-recursive

# Exclude certain patterns
python convert.py "A:\repos" --exclude __pycache__ .git node_modules

# Flat output (no subdirectories)
python convert.py "E:\Downloads" -o "C:\converted" --flat

# Save conversion report
python convert.py "E:\Downloads" --report conversion_report.json -v
```

### Using the Batch File

```powershell
# Windows - just double-click or run:
convert.bat "E:\Downloads" -o "A:\repos\markdown_exports"
```

## Examples for KingAI Projects

### Convert E:\Downloads PDFs to Markdown
```powershell
python convert.py "E:\Downloads" -o "A:\repos\kingai_markdown_exports" --extensions pdf docx -v
```

### Convert A:\repos documents
```powershell
python convert.py "A:\repos" -o "A:\repos\kingai_markdown_exports" --extensions pdf docx xlsx pptx -v --exclude __pycache__ .git
```

### Full KingAI document export (both drives)
```powershell
python convert.py "E:\Downloads" "A:\repos" -o "A:\repos\kingai_markdown_exports" --workers 12 --extensions pdf docx --report export_report.json -v
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

The converter uses multiple fallback methods:

1. **PDF**: pdfplumber → pdfminer → basic text extraction
2. **DOCX**: mammoth (HTML) → python-docx → basic extraction
3. **XLSX**: openpyxl + pandas → xlrd fallback

If a file fails, it's logged and the converter continues with the next file.

## Output Format

Each converted file includes:
- Source file comment header
- Timestamp of conversion
- Preserved document structure (headings, tables, lists)

Example output:
```markdown
<!-- Converted from: Service Report.pdf -->
<!-- Converted at: 2026-01-12T15:30:00 -->

# Service Report

**Client Code**: IGPAS004732
**Date**: 27 June 2025
...
```

## License

MIT - KingAI Pty Ltd

## Author

Jason King
- Email: jason.king@kingai.com.au
- Website: https://kingai.com.au

---

## v2.0 Features (January 2026)

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
python convert.py "E:\Downloads" --report conversion_report.json
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

## 🚀 Future Roadmap

### Advanced OCR & Image Processing
- **Diagram extraction**: Parse EEPROM pinout diagrams, PCB schematics, wiring diagrams
- **Technical drawing analysis**: Extract table data from scanned datasheets and service manuals
- **Handwritten notes**: OCR support for handwritten annotations on documents
- **Image-to-Markdown tables**: Convert complex tables in images to clean Markdown format

### Domain-Specific Extraction
- **Automotive ECU data**: Enhanced parsing for calibration tables, memory maps, addressing schemes (0x0000, $181E1)
- **Electronics datasheets**: Structured extraction of pinouts, timing diagrams, register maps
- **Scientific papers**: Better equation extraction (LaTeX/MathML), figure captions, references
- **Legal documents**: Clause numbering, citations, structured sections

### Semantic Enhancement
- **Keyword tagging**: Auto-tag content with domain terms (ALDL, OBD-II, KWP2000, HC11, XDF)
- **Cross-referencing**: Link hex addresses to definitions across multiple documents
- **Glossary generation**: Build terminology indexes for technical document collections
- **Context preservation**: Maintain relationships between figures, tables, and references

### Output Formats & Integrations
- **Vector database export**: Direct export to Pinecone, Weaviate, ChromaDB for RAG/LLM
- **Structured JSON**: JSON-LD with embedded metadata for knowledge graphs
- **LaTeX output**: Convert back to LaTeX for academic publishing
- **HTML with anchors**: Navigable HTML with table of contents and deep linking

### Performance & Scale
- **GPU acceleration**: Use CUDA for OCR and image processing on large batches
- **Cloud deployment**: Docker containers with API endpoints (REST/GraphQL)
- **Streaming processing**: Handle multi-GB PDFs without loading entire file into memory
- **Incremental updates**: Only re-process changed sections of documents

### Plugin Architecture
- **Custom extractors**: Python plugin system for domain-specific parsers
- **Template library**: Pre-built extraction templates for common document types
- **Post-processing hooks**: Custom filters for cleaning/enhancing extracted text

---

**Project homepage**: https://github.com/KingAiCodeForge/kingai_all_files_to_markdown_batch_converter
