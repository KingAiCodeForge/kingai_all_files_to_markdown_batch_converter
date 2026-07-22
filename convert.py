#!/usr/bin/env python3
"""
KingAI Markdown Converter v1.2
==============================
Batch document converter with quality metrics, multi-library fallbacks,
bounded per-file execution, and resumable large-collection processing.

Version 1.2 adds legacy and OpenDocument conversion through LibreOffice,
direct extraction of populated spreadsheet cells, RTF fragment recovery,
and page-image preservation for PDFs that contain no extractable text.

Author: Jason King (KingAI Pty Ltd)
License: MIT
Version: 1.2.0
"""

import argparse
import gc
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote
import multiprocessing
import os as _os
_os.environ.setdefault('PYMUPDF_SUGGEST_LAYOUT_ANALYZER', '0')

# Optional: memory monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Try to import tqdm for progress bars
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# Try to import rich for beautiful terminal output
try:
    from rich.console import Console
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TimeRemainingColumn,
        TimeElapsedColumn,
        MofNCompleteColumn,
    )
    from rich.table import Table
    from rich.panel import Panel
    RICH_AVAILABLE = True
    rconsole = Console()
except ImportError:
    RICH_AVAILABLE = False
    rconsole = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Version info
__version__ = "1.2.0"

# Supported file extensions
SUPPORTED_EXTENSIONS = {
    '.pdf': 'PDF Document',
    '.docx': 'Word Document',
    '.doc': 'Legacy Word Document',
    '.xlsx': 'Excel Spreadsheet',
    '.xls': 'Legacy Excel Spreadsheet',
    '.pptx': 'PowerPoint Presentation',
    '.ppt': 'Legacy PowerPoint',
    '.odt': 'OpenDocument Text',
    '.ods': 'OpenDocument Spreadsheet',
    '.odp': 'OpenDocument Presentation',
    '.epub': 'E-Book',
    '.html': 'HTML Document',
    '.htm': 'HTML Document',
    '.csv': 'CSV Data',
    '.json': 'JSON Data',
    '.xml': 'XML Document',
    '.msg': 'Outlook Message',
    '.eml': 'Email Message',
    '.ipynb': 'Jupyter Notebook',
    '.txt': 'Text File',
    '.md': 'Markdown File',
    '.rtf': 'Rich Text Format',
}

# --- v1.1: Pre-flight validation ---
# Minimum file sizes (bytes); files below this cannot be valid.
MIN_FILE_SIZES = {
    '.pdf': 100,    # %PDF-1.0 header + %%EOF
    '.docx': 1000,  # ZIP archive minimum
    '.doc': 512,    # OLE2 compound doc header
    '.xlsx': 1000,
    '.pptx': 1000,
}
DEFAULT_MIN_SIZE = 50  # catch zero-byte files

# Magic byte signatures for header validation
# Ref: https://en.wikipedia.org/wiki/List_of_file_signatures
FILE_SIGNATURES = {
    '.pdf': (b'%PDF-', 5),
    '.doc': (b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1', 8),
    '.xls': (b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1', 8),
    '.ppt': (b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1', 8),
    '.docx': (b'PK\x03\x04', 4),
    '.xlsx': (b'PK\x03\x04', 4),
    '.pptx': (b'PK\x03\x04', 4),
    '.rtf': (b'{\\rtf', 5),
}

# Batch processing defaults
DEFAULT_BATCH_SIZE = 200
DEFAULT_FILE_TIMEOUT_SECONDS = 300


class ErrorClass:
    """Categorize failures for actionable reporting."""
    INVALID_FILE = "invalid_file"
    CORRUPT_FILE = "corrupt_file"
    EXTRACTOR_CRASH = "extractor_crash"
    TIMEOUT = "timeout"
    MEMORY_ERROR = "memory_error"
    DEPENDENCY_MISSING = "dependency_missing"
    PERMISSION_ERROR = "permission_error"


def classify_error(error: Exception) -> str:
    """Classify an error for reporting."""
    msg = str(error).lower()
    if isinstance(error, MemoryError):
        return ErrorClass.MEMORY_ERROR
    if isinstance(error, PermissionError):
        return ErrorClass.PERMISSION_ERROR
    if isinstance(error, ImportError):
        return ErrorClass.DEPENDENCY_MISSING
    if "header" in msg or "magic" in msg or "not a pdf" in msg:
        return ErrorClass.INVALID_FILE
    if "corrupt" in msg or "damaged" in msg or "truncated" in msg:
        return ErrorClass.CORRUPT_FILE
    if "timeout" in msg:
        return ErrorClass.TIMEOUT
    return ErrorClass.EXTRACTOR_CRASH


def validate_file_preflight(file_path: Path) -> tuple:
    """
    Pre-flight validation: check file size and magic bytes.
    Returns (is_valid, reason).
    """
    ext = file_path.suffix.lower()

    # Size check
    try:
        size = file_path.stat().st_size
    except OSError as e:
        return False, f"cannot stat file: {e}"

    min_size = MIN_FILE_SIZES.get(ext, DEFAULT_MIN_SIZE)
    if size < min_size:
        return False, (
            f"too small for {ext}: {size} bytes "
            f"(minimum {min_size})"
        )

    # Magic byte check
    if ext in FILE_SIGNATURES:
        expected_magic, read_size = FILE_SIGNATURES[ext]
        try:
            with open(file_path, 'rb') as f:
                header = f.read(read_size)
        except (IOError, OSError) as e:
            return False, f"cannot read header: {e}"

        if len(header) < read_size:
            return False, (
                f"file too small for header check: "
                f"{len(header)} < {read_size} bytes"
            )
        if not header.startswith(expected_magic):
            if ext == '.rtf':
                try:
                    with open(file_path, 'rb') as f:
                        fragment_start = f.read(4096)
                        f.seek(max(0, size - 64))
                        fragment_end = f.read()
                    if b'\\par' in fragment_start and fragment_end.rstrip().endswith(b'}'):
                        return True, "valid RTF body fragment"
                except (IOError, OSError):
                    pass
            return False, (
                f"invalid {ext} header: expected "
                f"{expected_magic!r}, got {header[:read_size]!r}"
            )

    return True, "valid"


def full_jitter_delay(
    attempt: int, base: float = 0.5, cap: float = 5.0
) -> float:
    """
    AWS Full Jitter backoff.
    sleep = random(0, min(cap, base * 2^attempt))
    Ref: aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
    """
    exp_delay = min(cap, base * (2 ** attempt))
    return random.uniform(0, exp_delay)


class AdaptiveWorkerPool:
    """
    Monitor failure rate and adjust worker count.
    Uses a sliding window of recent results.
    """

    def __init__(self, initial_workers: int, window_size: int = 50):
        self.initial_workers = initial_workers
        self.current_workers = initial_workers
        self.window_size = window_size
        self.recent_results = deque(maxlen=window_size)

    def record_result(self, success: bool):
        self.recent_results.append(success)

    @property
    def failure_rate(self) -> float:
        if not self.recent_results:
            return 0.0
        failures = sum(1 for r in self.recent_results if not r)
        return failures / len(self.recent_results)

    def get_adjusted_workers(self) -> int:
        rate = self.failure_rate

        # Also check memory if psutil available
        if PSUTIL_AVAILABLE:
            mem = psutil.virtual_memory()
            if mem.percent > 90:
                rate = max(rate, 0.51)  # force safe mode
            elif mem.percent > 85:
                rate = max(rate, 0.16)  # force degraded

        if rate > 0.50:
            new = 1
            level = "SAFE MODE"
        elif rate > 0.30:
            new = 2
            level = "SURVIVAL"
        elif rate > 0.15:
            new = max(2, self.initial_workers // 2)
            level = "DEGRADED"
        elif rate > 0.05:
            new = max(2, int(self.initial_workers * 0.75))
            level = "REDUCED"
        else:
            new = self.initial_workers
            level = "NORMAL"

        if new != self.current_workers:
            logger.warning(
                f"Worker adjustment: {self.current_workers} -> "
                f"{new} ({level}, failure rate: {rate:.0%})"
            )
            self.current_workers = new

        return self.current_workers


def load_checkpoint(checkpoint_path: Path) -> dict:
    """Load checkpoint file if it exists."""
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_checkpoint(
    checkpoint_path: Path,
    completed: list,
    failed: dict,
    total: int
):
    """Save checkpoint for crash recovery."""
    data = {
        "version": __version__,
        "last_updated": datetime.now().isoformat(),
        "total_files": total,
        "completed": len(completed),
        "failed_count": len(failed),
        "completed_paths": completed,
        "failed_paths": failed,
    }
    try:
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass  # checkpoint is best-effort


@dataclass
class QualityMetrics:
    """Quality metrics for a conversion"""
    char_count: int = 0
    word_count: int = 0
    line_count: int = 0
    avg_line_length: float = 0.0
    table_count: int = 0
    heading_count: int = 0
    extraction_method: str = ""
    
    def to_dict(self) -> dict:
        return {
            'char_count': self.char_count,
            'word_count': self.word_count,
            'line_count': self.line_count,
            'avg_line_length': round(self.avg_line_length, 2),
            'table_count': self.table_count,
            'heading_count': self.heading_count,
            'extraction_method': self.extraction_method
        }


@dataclass
class ConversionResult:
    """Result of a single file conversion"""
    input_path: str
    output_path: str
    success: bool
    error_message: str = ""
    conversion_time: float = 0.0
    input_size: int = 0
    output_size: int = 0
    quality: Optional[QualityMetrics] = None


@dataclass
class BatchResult:
    """Result of a batch conversion operation"""
    total_files: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    total_time: float = 0.0
    results: List[ConversionResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def get_optimal_workers(max_workers: Optional[int] = None) -> int:
    """
    Get optimal number of worker processes for the system.
    Optimized for i9-9900K (8 cores / 16 threads)
    """
    cpu_count = multiprocessing.cpu_count()
    
    # For I/O bound tasks, use more workers than cores
    # But cap for memory safety with large PDFs
    optimal = min(cpu_count, 12)
    
    if max_workers:
        return min(max_workers, optimal)
    
    return optimal


def calculate_quality_metrics(text: str, method: str = "") -> QualityMetrics:
    """Calculate quality metrics for extracted text"""
    if not text:
        return QualityMetrics(extraction_method=method)
    
    lines = text.split('\n')
    words = text.split()
    
    # Count markdown elements
    table_count = text.count('|---') + text.count('| ---')
    heading_count = len(re.findall(r'^#{1,6}\s', text, re.MULTILINE))
    
    avg_line_len = len(text) / len(lines) if lines else 0
    
    return QualityMetrics(
        char_count=len(text),
        word_count=len(words),
        line_count=len(lines),
        avg_line_length=avg_line_len,
        table_count=table_count,
        heading_count=heading_count,
        extraction_method=method
    )


def extract_with_markitdown(file_path: Path) -> Tuple[str, str]:
    """Extract using Microsoft's MarkItDown library"""
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(file_path))
        markdown = result.markdown
        if not markdown or not markdown.strip():
            raise RuntimeError("MarkItDown returned no text")
        return markdown, "markitdown"
    except ImportError:
        raise
    except Exception as e:
        raise RuntimeError(f"MarkItDown failed: {e}")


def find_libreoffice_executable() -> Optional[Path]:
    """Find a usable LibreOffice command without requiring PATH changes."""
    configured = os.environ.get("LIBREOFFICE_PATH")
    candidates = [Path(configured)] if configured else []

    for command in ("soffice", "libreoffice"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))

    if os.name == "nt":
        for root_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(root_name)
            if root:
                program_dir = Path(root) / "LibreOffice" / "program"
                candidates.extend(
                    [program_dir / "soffice.com", program_dir / "soffice.exe"]
                )

    return next((path for path in candidates if path.is_file()), None)


def _markdown_cell(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        try:
            value = value.isoformat()
        except (TypeError, ValueError):
            pass
    return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _column_name(column_number: int) -> str:
    name = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _render_sheet(name: str, rows) -> str:
    populated_rows = [(number, values) for number, values in rows if values]
    if not populated_rows:
        return ""

    columns = sorted(
        {
            column
            for _, values in populated_rows
            for column, value in values.items()
            if value is not None
        }
    )
    if not columns:
        return ""

    lines = [f"## Sheet: {name}", ""]
    headers = ["Row", *(_column_name(column) for column in columns)]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row_number, values in populated_rows:
        rendered = [_markdown_cell(values.get(column)) for column in columns]
        lines.append(f"| {row_number} | " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def extract_spreadsheet_to_markdown(file_path: Path) -> Tuple[str, str]:
    """Extract real populated cells without trusting inflated sheet dimensions."""
    sections = []
    extension = file_path.suffix.lower()

    if extension == ".xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(file_path, read_only=False, data_only=False)
        try:
            for worksheet in workbook.worksheets:
                values_by_row = {}
                for cell in worksheet._cells.values():
                    if cell.value is not None:
                        values_by_row.setdefault(cell.row, {})[cell.column] = cell.value
                section = _render_sheet(
                    worksheet.title,
                    sorted(values_by_row.items()),
                )
                if section:
                    sections.append(section)
        finally:
            workbook.close()
        method = "openpyxl-populated-cells"
    elif extension == ".xls":
        import xlrd

        workbook = xlrd.open_workbook(str(file_path), on_demand=True)
        try:
            for worksheet in workbook.sheets():
                rows = []
                for row_index in range(worksheet.nrows):
                    values = {
                        column_index + 1: worksheet.cell_value(row_index, column_index)
                        for column_index in range(worksheet.ncols)
                        if worksheet.cell_value(row_index, column_index) not in (None, "")
                    }
                    if values:
                        rows.append((row_index + 1, values))
                section = _render_sheet(worksheet.name, rows)
                if section:
                    sections.append(section)
        finally:
            workbook.release_resources()
        method = "xlrd-populated-cells"
    else:
        raise ValueError(f"Direct spreadsheet extraction does not support {extension}")

    if not sections:
        raise RuntimeError("Spreadsheet contains no populated cells")
    return "\n\n".join(sections), method


def extract_rtf_to_markdown(file_path: Path) -> Tuple[str, str]:
    """Extract normal RTF files and logger-produced RTF body fragments."""
    from striprtf.striprtf import rtf_to_text

    rtf_text = file_path.read_text(encoding="latin-1")
    if not rtf_text.lstrip().startswith("{\\rtf"):
        header = (
            r"{\rtf1\ansi\deff0{\fonttbl{\f0 Courier New;}}"
            r"{\colortbl;\red0\green0\blue0;\red0\green0\blue255;"
            r"\red255\green0\blue0;}\f0 "
        )
        rtf_text = header + rtf_text

    plain_text = rtf_to_text(rtf_text).strip()
    if not plain_text:
        raise RuntimeError("RTF extraction returned no text")
    return f"```text\n{plain_text}\n```", "striprtf"


def render_pdf_pages_to_markdown(
    file_path: Path,
    output_path: Path,
) -> Tuple[str, str]:
    """Preserve image-only PDFs as page images linked from Markdown."""
    import fitz

    asset_dir = output_path.parent / f"{output_path.stem}_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {file_path.name}",
        "",
        "> No extractable text was found. Pages are preserved as images.",
        "",
    ]

    with fitz.open(file_path) as document:
        if document.page_count == 0:
            raise RuntimeError("PDF has no pages to render")
        for page_number, page in enumerate(document, 1):
            image_name = f"page_{page_number:04d}.png"
            image_path = asset_dir / image_name
            temp_path = asset_dir / f".{image_name}.tmp.png"
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pixmap.save(str(temp_path))
            temp_path.replace(image_path)
            relative_image = f"{quote(asset_dir.name)}/{quote(image_name)}"
            lines.extend(
                [
                    f"## Page {page_number}",
                    "",
                    f"![Page {page_number}]({relative_image})",
                    "",
                ]
            )

    return "\n".join(lines), "pymupdf-page-images"


def extract_with_libreoffice(file_path: Path) -> Tuple[str, str]:
    """Convert legacy/OpenDocument Office files, then extract the modern file."""
    executable = find_libreoffice_executable()
    if not executable:
        raise RuntimeError(
            "LibreOffice was not found; install it or set LIBREOFFICE_PATH"
        )

    target_extensions = {
        ".doc": "docx",
        ".odt": "docx",
        ".rtf": "docx",
        ".xls": "xlsx",
        ".ods": "xlsx",
        ".ppt": "pptx",
        ".odp": "pptx",
    }
    target_extension = target_extensions.get(file_path.suffix.lower())
    if not target_extension:
        raise ValueError(f"LibreOffice fallback does not support {file_path.suffix}")

    with tempfile.TemporaryDirectory(prefix="kingai_libreoffice_") as temp_dir:
        temp_path = Path(temp_dir)
        profile_path = temp_path / "profile"
        profile_path.mkdir()
        command = [
            str(executable),
            f"-env:UserInstallation={profile_path.as_uri()}",
            "--headless",
            "--convert-to",
            target_extension,
            "--outdir",
            str(temp_path),
            str(file_path),
        ]
        run_options = {
            "capture_output": True,
            "text": True,
            "timeout": 120,
            "check": False,
        }
        if os.name == "nt":
            run_options["creationflags"] = subprocess.CREATE_NO_WINDOW
        completed = subprocess.run(command, **run_options)
        converted_path = temp_path / f"{file_path.stem}.{target_extension}"
        if completed.returncode != 0 or not converted_path.is_file():
            detail = (completed.stderr or completed.stdout or "no output").strip()
            raise RuntimeError(
                f"LibreOffice conversion failed ({completed.returncode}): {detail}"
            )

        if target_extension == "xlsx":
            markdown, method = extract_spreadsheet_to_markdown(converted_path)
        else:
            markdown, method = extract_with_markitdown(converted_path)
        return markdown, f"libreoffice-{target_extension}+{method}"


def extract_with_pymupdf4llm(file_path: Path) -> Tuple[str, str]:
    """Extract using PyMuPDF4LLM (optimized for LLM/RAG).
    
    If pymupdf-layout is installed, activates its ONNX-based document
    layout analyzer for improved page structure detection. The layout
    package must be imported BEFORE pymupdf4llm to activate; pymupdf
    does not auto-import its own subpackage.
    """
    try:
        # Activate ONNX layout analyzer if available (must happen before pymupdf4llm import)
        try:
            import pymupdf.layout  # noqa: F401 - sets pymupdf._get_layout
        except ImportError:
            pass  # pymupdf-layout is optional; use standard layout analysis.
        import pymupdf4llm
        md_text = pymupdf4llm.to_markdown(str(file_path))
        method = "pymupdf4llm+layout" if hasattr(pymupdf4llm, 'parse_document') else "pymupdf4llm"
        return md_text, method
    except ImportError:
        raise
    except Exception as e:
        raise RuntimeError(f"PyMuPDF4LLM failed: {e}")


def extract_with_pymupdf(file_path: Path) -> Tuple[str, str]:
    """Extract using PyMuPDF directly"""
    try:
        try:
            import pymupdf.layout  # noqa: F401 - suppress layout warning
        except ImportError:
            pass
        import pymupdf
        doc = pymupdf.open(str(file_path))
        text_parts = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                text_parts.append(text)
        doc.close()
        return '\n\n'.join(text_parts), "pymupdf"
    except ImportError:
        raise
    except Exception as e:
        raise RuntimeError(f"PyMuPDF failed: {e}")


def extract_with_pdfplumber(file_path: Path) -> Tuple[str, str]:
    """Extract using pdfplumber"""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(str(file_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text and text.strip():
                    text_parts.append(text)
        return '\n\n'.join(text_parts), "pdfplumber"
    except ImportError:
        raise
    except Exception as e:
        raise RuntimeError(f"pdfplumber failed: {e}")


def convert_pdf_with_best_quality(
    file_path: Path, 
    verbose: bool = False
) -> Tuple[str, QualityMetrics]:
    """
    Try multiple PDF extraction methods and return the best result.
    Best = highest word count (most complete extraction).
    v1.1: Adds exponential backoff with Full Jitter between failures.
    """
    results = []
    errors = []
    
    # Try each extraction method
    extractors = [
        ("markitdown", extract_with_markitdown),
        ("pymupdf4llm", extract_with_pymupdf4llm),
        ("pymupdf", extract_with_pymupdf),
        ("pdfplumber", extract_with_pdfplumber),
    ]
    
    for attempt, (name, extractor) in enumerate(extractors):
        try:
            text, method = extractor(file_path)
            metrics = calculate_quality_metrics(text, method)
            if metrics.word_count > 0:
                results.append((text, metrics))
            if verbose:
                logger.debug(f"  {name}: {metrics.word_count} words")
        except ImportError:
            continue  # Library not installed
        except Exception as e:
            errors.append(f"{name}: {e}")
            # Exponential backoff with jitter before next attempt
            if attempt < len(extractors) - 1:
                delay = full_jitter_delay(attempt)
                if delay > 0.01:
                    time.sleep(delay)
            continue
    
    if not results:
        if errors:
            raise RuntimeError(
                f"All extractors failed: {'; '.join(errors)}"
            )
        raise RuntimeError(
            "PDF extractors returned no text; the document may be "
            "image-only, empty, or require OCR"
        )
    
    # Return result with highest word count
    best = max(results, key=lambda x: x[1].word_count)
    return best


def convert_single_file(
    args: Tuple[Path, Path, bool, bool]
) -> ConversionResult:
    """
    Convert a single file to markdown with quality metrics.
    Uses best available extraction method.
    This function runs in a separate process.
    """
    input_path, output_path, overwrite, verbose = args
    
    start_time = time.time()
    input_path = Path(input_path)
    output_path = Path(output_path)
    
    try:
        # Check if output already exists
        if output_path.exists() and not overwrite:
            return ConversionResult(
                input_path=str(input_path),
                output_path=str(output_path),
                success=True,
                error_message="Skipped (already exists)",
                conversion_time=0.0,
                input_size=input_path.stat().st_size,
                output_size=output_path.stat().st_size
            )
        
        # v1.1: Pre-flight validation
        valid, reason = validate_file_preflight(input_path)
        if not valid:
            return ConversionResult(
                input_path=str(input_path),
                output_path=str(output_path),
                success=False,
                error_message=f"Pre-flight: {reason}",
                conversion_time=time.time() - start_time,
                input_size=0
            )
        
        # Get input file size
        input_size = input_path.stat().st_size
        
        # Create output directory if needed
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Determine extraction method based on file type
        ext = input_path.suffix.lower()
        
        if ext == '.pdf':
            try:
                markdown_text, quality = convert_pdf_with_best_quality(
                    input_path, verbose
                )
            except RuntimeError as extraction_error:
                try:
                    markdown_text, method = render_pdf_pages_to_markdown(
                        input_path,
                        output_path,
                    )
                except Exception as render_error:
                    raise RuntimeError(
                        f"PDF text extraction failed: {extraction_error}; "
                        f"page rendering failed: {render_error}"
                    ) from render_error
                quality = calculate_quality_metrics(markdown_text, method)
        elif ext == '.xlsx':
            try:
                markdown_text, method = extract_spreadsheet_to_markdown(input_path)
            except Exception as primary_error:
                try:
                    markdown_text, method = extract_with_markitdown(input_path)
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"Spreadsheet extraction failed: {primary_error}; "
                        f"MarkItDown fallback failed: {fallback_error}"
                    ) from fallback_error
            quality = calculate_quality_metrics(markdown_text, method)
        elif ext == '.xls':
            try:
                markdown_text, method = extract_spreadsheet_to_markdown(input_path)
            except Exception:
                markdown_text, method = extract_with_libreoffice(input_path)
            quality = calculate_quality_metrics(markdown_text, method)
        elif ext == '.rtf':
            try:
                markdown_text, method = extract_rtf_to_markdown(input_path)
            except Exception as primary_error:
                try:
                    markdown_text, method = extract_with_libreoffice(input_path)
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"RTF extraction failed: {primary_error}; "
                        f"LibreOffice fallback failed: {fallback_error}"
                    ) from fallback_error
            quality = calculate_quality_metrics(markdown_text, method)
        elif ext in {'.doc', '.odt', '.ods', '.ppt', '.odp'}:
            try:
                markdown_text, method = extract_with_markitdown(input_path)
            except Exception as primary_error:
                try:
                    markdown_text, method = extract_with_libreoffice(input_path)
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"Primary extraction failed: {primary_error}; "
                        f"LibreOffice fallback failed: {fallback_error}"
                    ) from fallback_error
            quality = calculate_quality_metrics(markdown_text, method)
        else:
            # Use MarkItDown for other formats
            markdown_text, method = extract_with_markitdown(input_path)
            quality = calculate_quality_metrics(markdown_text, method)
        
        # Write output with metadata header
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"<!-- Converted from: {input_path.name} -->\n")
            f.write(f"<!-- Converted at: {datetime.now().isoformat()} -->\n")
            f.write(f"<!-- Method: {quality.extraction_method} -->\n")
            f.write(f"<!-- Words: {quality.word_count} | ")
            f.write(f"Chars: {quality.char_count} | ")
            f.write(f"Lines: {quality.line_count} -->\n\n")
            f.write(markdown_text)
        
        conversion_time = time.time() - start_time
        output_size = output_path.stat().st_size
        
        return ConversionResult(
            input_path=str(input_path),
            output_path=str(output_path),
            success=True,
            conversion_time=conversion_time,
            input_size=input_size,
            output_size=output_size,
            quality=quality
        )
        
    except ImportError as e:
        return ConversionResult(
            input_path=str(input_path),
            output_path=str(output_path),
            success=False,
            error_message=f"Missing dependency: {e}",
            conversion_time=time.time() - start_time
        )
        
    except Exception as e:
        return ConversionResult(
            input_path=str(input_path),
            output_path=str(output_path),
            success=False,
            error_message=str(e),
            conversion_time=time.time() - start_time,
            input_size=input_path.stat().st_size if input_path.exists() else 0
        )


def find_documents(
    root_dirs: List[str],
    extensions: Optional[List[str]] = None,
    recursive: bool = True,
    exclude_patterns: Optional[List[str]] = None
) -> List[Path]:
    """
    Find all documents in the specified directories.
    """
    if extensions is None:
        extensions = list(SUPPORTED_EXTENSIONS.keys())
    
    # Normalize extensions
    extensions = [ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in extensions]
    
    exclude_patterns = exclude_patterns or []
    documents = []
    
    for root_dir in root_dirs:
        root_path = Path(root_dir)
        
        if not root_path.exists():
            logger.warning(f"Directory does not exist: {root_dir}")
            continue
        
        if not root_path.is_dir():
            # Single file
            if root_path.suffix.lower() in extensions:
                documents.append(root_path)
            continue
        
        # Default directories to always skip (massive and never contain docs)
        _ALWAYS_SKIP_DIRS = {'.git', '.vs', 'node_modules', '__pycache__', '.venv', 'venv',
                             '.tox', '.mypy_cache', '.pytest_cache', '.eggs',
                             '.hg', '.svn', '.bzr'}
        
        # Use os.walk for directory pruning (much faster than glob)
        if recursive:
            scan_count = 0
            for dirpath, dirnames, filenames in os.walk(root_path):
                scan_count += 1
                if scan_count % 500 == 0:
                    logger.info(
                        f"  ... scanned {scan_count} dirs, "
                        f"{len(documents)} docs found so far"
                    )
                # Prune directories in-place to avoid traversing them
                dirnames[:] = [
                    d for d in dirnames
                    if d not in _ALWAYS_SKIP_DIRS
                    and not any(excl in os.path.join(dirpath, d) for excl in exclude_patterns)
                ]
                
                for fname in filenames:
                    file_path = Path(dirpath) / fname
                    if file_path.suffix.lower() not in extensions:
                        continue
                    # Check exclusions on the full path
                    skip = False
                    for exclude in exclude_patterns:
                        if exclude in str(file_path):
                            skip = True
                            break
                    if not skip:
                        documents.append(file_path)
        else:
            for file_path in root_path.iterdir():
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in extensions:
                    continue
                skip = False
                for exclude in exclude_patterns:
                    if exclude in str(file_path):
                        skip = True
                        break
                if not skip:
                    documents.append(file_path)
    
    return documents


def get_output_path(
    input_path: Path,
    output_dir: Optional[Path] = None,
    preserve_structure: bool = True,
    base_dir: Optional[Path] = None
) -> Path:
    """
    Generate output path for a converted file.
    Default: Output goes next to original file with .md extension.
    """
    if output_dir:
        if preserve_structure and base_dir:
            # Preserve relative directory structure
            try:
                relative_path = input_path.relative_to(base_dir)
                output_path = output_dir / relative_path.with_suffix('.md')
            except ValueError:
                output_path = output_dir / input_path.with_suffix('.md').name
        else:
            output_path = output_dir / input_path.with_suffix('.md').name
    else:
        # Same directory as input (default behavior)
        output_path = input_path.with_suffix('.md')
    
    return output_path


def terminate_executor_workers(executor: ProcessPoolExecutor) -> None:
    """Best-effort hard stop for workers stuck inside third-party parsers."""
    terminate_workers = getattr(executor, "terminate_workers", None)
    if callable(terminate_workers):
        terminate_workers()
        return

    processes = getattr(executor, "_processes", None)
    if not processes:
        executor.shutdown(wait=False, cancel_futures=True)
        return

    for process in list(processes.values()):
        if process.is_alive():
            process.terminate()

    for process in list(processes.values()):
        process.join(timeout=2)


def batch_convert(
    input_dirs: List[str],
    output_dir: Optional[str] = None,
    extensions: Optional[List[str]] = None,
    recursive: bool = True,
    overwrite: bool = False,
    max_workers: Optional[int] = None,
    verbose: bool = False,
    exclude_patterns: Optional[List[str]] = None,
    preserve_structure: bool = True,
    dry_run: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    resume: bool = False,
    log_file: Optional[str] = None,
    file_timeout: int = DEFAULT_FILE_TIMEOUT_SECONDS,
) -> BatchResult:
    """
    Convert multiple documents to markdown using multiprocessing.
    v1.1: Batch chunking, checkpoints, adaptive workers, resume.
    """
    start_time = time.time()
    result = BatchResult()
    
    # Find all documents
    logger.info("Scanning for documents...")
    documents = find_documents(
        input_dirs, extensions, recursive, exclude_patterns
    )
    result.total_files = len(documents)
    
    if not documents:
        logger.info("No documents found to convert.")
        return result
    
    logger.info(f"Found {len(documents)} documents to process")
    
    # Prepare output directory
    output_path = Path(output_dir) if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)
    
    # v1.1: Set up file-based logging
    if log_file or output_path:
        log_path = Path(log_file) if log_file else (
            output_path / "conversion_progress.log"
        )
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(
                str(log_path), encoding='utf-8'
            )
            fh.setFormatter(logging.Formatter(
                '%(asctime)s | %(levelname)-7s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            logger.addHandler(fh)
            logger.info(f"Logging to: {log_path}")
        except OSError:
            pass  # file logging is best-effort
    
    # Determine base directory for structure preservation
    base_dir = None
    if preserve_structure and len(input_dirs) == 1:
        base_dir = Path(input_dirs[0])
    
    # v1.1: Checkpoint for resume
    checkpoint_path = None
    completed_paths_set = set()
    if output_path:
        checkpoint_path = output_path / ".convert_checkpoint.json"
    
    if resume and checkpoint_path:
        ckpt = load_checkpoint(checkpoint_path)
        if ckpt.get("completed_paths"):
            completed_paths_set = set(ckpt["completed_paths"])
            logger.info(
                f"Resuming: {len(completed_paths_set)} files "
                f"already completed"
            )
    
    # Prepare conversion tasks
    tasks = []
    for doc_path in documents:
        out_path = get_output_path(
            doc_path, output_path, preserve_structure, base_dir
        )
        
        # A checkpoint is only valid while its expected output still exists.
        # This also repairs checkpoints left inconsistent by interrupted or
        # overlapping conversion runs.
        if str(doc_path) in completed_paths_set:
            if out_path.exists():
                result.skipped += 1
                continue
            completed_paths_set.discard(str(doc_path))
            logger.warning(
                f"Checkpoint output missing; retrying: {doc_path.name}"
            )
        
        # Skip if output exists and not overwriting
        if out_path.exists() and not overwrite:
            result.skipped += 1
            if verbose:
                logger.info(f"Skipped (exists): {doc_path.name}")
            continue
        
        tasks.append((doc_path, out_path, overwrite, verbose))
    
    if not tasks:
        logger.info(
            "All files already converted "
            "(use --overwrite to reconvert)"
        )
        result.total_time = time.time() - start_time
        return result
    
    if dry_run:
        logger.info(f"DRY RUN: Would convert {len(tasks)} files")
        for doc_path, out_path, _, _ in tasks[:10]:
            logger.info(f"   {doc_path.name} -> {out_path.name}")
        if len(tasks) > 10:
            logger.info(f"   ... and {len(tasks) - 10} more")
        result.total_time = time.time() - start_time
        return result
    
    # Get optimal worker count
    num_workers = get_optimal_workers(max_workers)
    
    # v1.1: Adaptive worker pool
    worker_pool = AdaptiveWorkerPool(num_workers)

    # A future's timeout clock starts when it is submitted. Keep each pool
    # wave no larger than the worker count so queued documents cannot expire
    # before a worker has had a chance to open them.
    requested_batch_size = batch_size
    batch_size = max(1, min(batch_size, num_workers))
    if batch_size != requested_batch_size:
        logger.info(
            "Capped execution wave from "
            f"{requested_batch_size} to {batch_size} files "
            "to preserve per-file timeout semantics"
        )
    
    logger.info(
        f"Converting {len(tasks)} files with "
        f"{num_workers} workers "
        f"(batch size: {batch_size})..."
    )
    
    # v1.1: Startup banner
    if RICH_AVAILABLE:
        info = Table.grid(padding=(0, 2))
        info.add_column(style="cyan", justify="right")
        info.add_column(style="white")
        info.add_row("Files:", f"[bold]{len(tasks)}[/bold] to convert")
        info.add_row("Workers:", f"[bold]{num_workers}[/bold]")
        info.add_row("Batch size:", f"{batch_size}")
        if resume and completed_paths_set:
            info.add_row(
                "Resumed:",
                f"[green]{len(completed_paths_set)}[/green] "
                f"already done"
            )
        if output_path:
            info.add_row("Output:", str(output_path))
        rconsole.print(Panel(
            info,
            title=(
                f"[bold cyan]KingAI Markdown Converter "
                f"v{__version__}[/bold cyan]"
            ),
            border_style="cyan"
        ))
    else:
        print()
    
    # v1.1: Process in batches with checkpoints
    completed_list = list(completed_paths_set)
    failed_dict = {}
    total_tasks = len(tasks)
    completed_count = 0
    # Set up progress display
    use_rich_progress = RICH_AVAILABLE
    rich_progress = None
    rich_task = None
    pbar = None
    
    if use_rich_progress:
        rich_progress = Progress(
            SpinnerColumn(),
            TextColumn(
                "[bold blue]{task.description}[/bold blue]"
            ),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("|"),
            TimeElapsedColumn(),
            TextColumn("|"),
            TimeRemainingColumn(),
            console=rconsole,
            transient=False,
        )
        rich_progress.start()
        rich_task = rich_progress.add_task(
            "[cyan]Converting...", total=total_tasks
        )
    elif TQDM_AVAILABLE:
        pbar = tqdm(
            total=total_tasks,
            desc="Converting",
            unit="file",
            bar_format="{l_bar}{bar:30}{r_bar}",
            ncols=100,
            colour="green"
        )

    def record_timeout(task, message: Optional[str] = None) -> None:
        """Record a timed-out task and keep progress/checkpoints coherent."""
        nonlocal completed_count
        completed_count += 1
        result.failed += 1
        worker_pool.record_result(False)
        timeout_message = message or f"Timeout (>{file_timeout}s)"
        failed_dict[str(task[0])] = timeout_message
        result.errors.append(
            f"[timeout] {task[0]}: {timeout_message}"
        )

        if use_rich_progress and rich_progress:
            rich_progress.advance(rich_task)
            rconsole.print(
                f"  [yellow]TIMEOUT[/yellow] "
                f"[white]TIMEOUT: "
                f"{Path(task[0]).name}[/white]"
            )
        elif TQDM_AVAILABLE and pbar:
            pbar.update(1)
            tqdm.write(
                f"[{completed_count}/{total_tasks}] "
                f"TIMEOUT: {Path(task[0]).name}"
            )
        else:
            pct = (
                completed_count / total_tasks
            ) * 100
            print(
                f"[{pct:5.1f}%] "
                f"[{completed_count}/{total_tasks}]"
                f" TIMEOUT: {Path(task[0]).name}"
            )
        sys.stdout.flush()
    
    batch_start = 0
    wave_num = 0
    while batch_start < total_tasks:
        # Recalculate each wave after the previous results so adaptive worker
        # reductions never leave queued files aging against their timeout.
        adj_workers = worker_pool.get_adjusted_workers()
        wave_size = max(1, min(batch_size, adj_workers))
        batch_end = min(batch_start + wave_size, total_tasks)
        batch = tasks[batch_start:batch_end]
        wave_num += 1

        logger.info(
            f"Wave {wave_num}: files {batch_start + 1}-{batch_end} "
            f"of {total_tasks} with {adj_workers} worker(s)"
        )
        
        with ProcessPoolExecutor(
            max_workers=adj_workers
        ) as executor:
            futures = {
                executor.submit(convert_single_file, task): task
                for task in batch
            }
            pending_futures = set(futures)
            future_start_times = {
                future: time.monotonic()
                for future in pending_futures
            }

            while pending_futures:
                done, _ = wait(
                    pending_futures,
                    timeout=1,
                    return_when=FIRST_COMPLETED,
                )

                if not done:
                    if file_timeout and file_timeout > 0:
                        now = time.monotonic()
                        expired = [
                            future
                            for future in pending_futures
                            if now - future_start_times[future] >=
                            file_timeout
                        ]
                        if expired:
                            for future in expired:
                                task = futures[future]
                                record_timeout(task)
                                pending_futures.remove(future)
                                future.cancel()

                            terminate_executor_workers(executor)

                            for future in list(pending_futures):
                                task = futures[future]
                                record_timeout(
                                    task,
                                    (
                                        "Worker pool stopped after "
                                        "another file timed out"
                                    ),
                                )
                                pending_futures.remove(future)
                                future.cancel()
                            break
                    continue

                future = next(iter(done))
                pending_futures.remove(future)
                completed_count += 1
                try:
                    conv_result = future.result()
                    result.results.append(conv_result)
                    
                    if conv_result.success:
                        if "Skipped" in conv_result.error_message:
                            result.skipped += 1
                            worker_pool.record_result(True)
                        else:
                            result.successful += 1
                            worker_pool.record_result(True)
                            completed_list.append(
                                conv_result.input_path
                            )
                            q = conv_result.quality
                            words = q.word_count if q else 0
                            method = (
                                q.extraction_method if q
                                else "unknown"
                            )
                            status = (
                                f"{Path(conv_result.input_path).name}"
                            )
                            detail = (
                                f"   -> {words:,} words | "
                                f"{conv_result.conversion_time:.1f}s"
                                f" | {method}"
                            )
                    else:
                        result.failed += 1
                        worker_pool.record_result(False)
                        err_class = classify_error(
                            Exception(conv_result.error_message)
                        )
                        result.errors.append(
                            f"[{err_class}] "
                            f"{conv_result.input_path}: "
                            f"{conv_result.error_message}"
                        )
                        failed_dict[conv_result.input_path] = (
                            conv_result.error_message
                        )
                        status = (
                            f"FAIL: "
                            f"{Path(conv_result.input_path).name}"
                        )
                        detail = (
                            f"   -> [{err_class}] "
                            f"{conv_result.error_message[:60]}"
                        )
                    
                    # Progress display
                    if use_rich_progress and rich_progress:
                        rich_progress.advance(rich_task)
                        name = Path(
                            conv_result.input_path
                        ).name
                        short = (
                            name[:45] + "..."
                            if len(name) > 45 else name
                        )
                        rich_progress.update(
                            rich_task,
                            description=f"[cyan]{short}"
                        )
                        if conv_result.success and \
                                "Skipped" not in \
                                conv_result.error_message:
                            rconsole.print(
                                f"  [green]OK[/green] "
                                f"[white]{status}[/white] "
                                f"[dim]{detail.strip()}[/dim]"
                            )
                        elif not conv_result.success:
                            rconsole.print(
                                f"  [red]FAIL[/red] "
                                f"[white]{status}[/white] "
                                f"[dim]{detail.strip()}[/dim]"
                            )
                    elif TQDM_AVAILABLE and pbar:
                        pbar.update(1)
                        if conv_result.success and \
                                "Skipped" not in \
                                conv_result.error_message:
                            tqdm.write(
                                f"[{completed_count}/{total_tasks}]"
                                f" OK {status}"
                            )
                            tqdm.write(detail)
                        elif not conv_result.success:
                            tqdm.write(
                                f"[{completed_count}/{total_tasks}]"
                                f" FAIL {status}"
                            )
                            tqdm.write(detail)
                    else:
                        pct = (
                            completed_count / total_tasks
                        ) * 100
                        if conv_result.success and \
                                "Skipped" not in \
                                conv_result.error_message:
                            print(
                                f"[{pct:5.1f}%] "
                                f"[{completed_count}/{total_tasks}]"
                                f" OK {status}"
                            )
                            print(detail)
                        elif not conv_result.success:
                            print(
                                f"[{pct:5.1f}%] "
                                f"[{completed_count}/{total_tasks}]"
                                f" FAIL {status}"
                            )
                            print(detail)
                    
                    sys.stdout.flush()
                        
                except TimeoutError:
                    task = futures[future]
                    completed_count -= 1
                    record_timeout(task)
                except Exception as e:
                    result.failed += 1
                    worker_pool.record_result(False)
                    task = futures[future]
                    failed_dict[str(task[0])] = str(e)
                    result.errors.append(
                        f"[{classify_error(e)}] {task[0]}: {e}"
                    )
                    if use_rich_progress and rich_progress:
                        rich_progress.advance(rich_task)
                        rconsole.print(
                            f"  [red]FAIL[/red] "
                            f"[white]ERROR: "
                            f"{Path(task[0]).name}[/white] "
                            f"[dim]{str(e)[:50]}[/dim]"
                        )
                    elif TQDM_AVAILABLE and pbar:
                        pbar.update(1)
                        tqdm.write(
                            f"[{completed_count}/{total_tasks}] "
                            f"ERROR: {Path(task[0]).name}: {e}"
                        )
                    else:
                        pct = (
                            completed_count / total_tasks
                        ) * 100
                        print(
                            f"[{pct:5.1f}%] "
                            f"[{completed_count}/{total_tasks}]"
                            f" ERROR: {Path(task[0]).name}: {e}"
                        )
                    sys.stdout.flush()
        
        # v1.1: Save checkpoint between batches
        if checkpoint_path:
            save_checkpoint(
                checkpoint_path,
                completed_list,
                failed_dict,
                total_tasks
            )
        
        # GC between batches to release memory
        gc.collect()
        
        # Log memory usage between batches
        if PSUTIL_AVAILABLE and total_tasks > batch_size:
            mem = psutil.virtual_memory()
            logger.info(
                f"Memory: {mem.percent}% used "
                f"({mem.available / 1_073_741_824:.1f}GB free)"
            )

        batch_start = batch_end
    
    # Close progress display
    if use_rich_progress and rich_progress:
        rich_progress.stop()
    elif TQDM_AVAILABLE and pbar:
        pbar.close()
    
    result.total_time = time.time() - start_time
    
    # Build error breakdown
    error_classes = {}
    if result.errors:
        for err in result.errors:
            if err.startswith("["):
                cls = err[1:err.index("]")]
                error_classes[cls] = (
                    error_classes.get(cls, 0) + 1
                )
    
    avg_time = (
        result.total_time / result.successful
        if result.successful > 0 else 0
    )
    
    # Rich summary panel
    if RICH_AVAILABLE:
        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="cyan", justify="right")
        summary.add_column(style="white")
        summary.add_row(
            "Total:", f"{result.total_files} files"
        )
        summary.add_row(
            "Successful:",
            f"[bold green]{result.successful}[/bold green]"
        )
        summary.add_row(
            "Skipped:", f"[dim]{result.skipped}[/dim]"
        )
        summary.add_row(
            "Failed:",
            f"[bold red]{result.failed}[/bold red]"
            if result.failed > 0
            else "[green]0[/green]"
        )
        summary.add_row(
            "Time:",
            f"{result.total_time:.1f}s"
            + (f" ({avg_time:.2f}s/file)" if avg_time else "")
        )
        if error_classes:
            err_parts = []
            for cls, cnt in sorted(
                error_classes.items(), key=lambda x: -x[1]
            ):
                err_parts.append(f"{cls}: {cnt}")
            summary.add_row(
                "Errors:",
                "[red]" + ", ".join(err_parts) + "[/red]"
            )
        
        border = (
            "green" if result.failed == 0 else "yellow"
        )
        title_text = (
            "[bold green]CONVERSION COMPLETE[/bold green]"
            if result.failed == 0
            else "[bold yellow]CONVERSION COMPLETE "
            "(with errors)[/bold yellow]"
        )
        rconsole.print()
        rconsole.print(Panel(
            summary,
            title=title_text,
            border_style=border
        ))
    
    # Always log to file/console too (for log file)
    logger.info("=" * 60)
    logger.info("CONVERSION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"   Total files:  {result.total_files}")
    logger.info(f"   Successful:   {result.successful}")
    logger.info(f"   Skipped:      {result.skipped}")
    logger.info(f"   Failed:       {result.failed}")
    logger.info(f"   Total time:   {result.total_time:.2f}s")
    if avg_time:
        logger.info(f"   Avg time:     {avg_time:.2f}s per file")
    if error_classes:
        logger.info("   Error breakdown:")
        for cls, count in sorted(
            error_classes.items(),
            key=lambda x: -x[1]
        ):
            logger.info(f"     {cls}: {count}")
    
    return result


def save_report(result: BatchResult, output_path: str):
    """Save conversion report to JSON with quality metrics."""
    
    # Calculate aggregate quality stats
    total_words = 0
    total_chars = 0
    methods_used = {}
    
    for r in result.results:
        if r.quality:
            total_words += r.quality.word_count
            total_chars += r.quality.char_count
            method = r.quality.extraction_method
            methods_used[method] = methods_used.get(method, 0) + 1
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'version': __version__,
        'summary': {
            'total_files': result.total_files,
            'successful': result.successful,
            'failed': result.failed,
            'skipped': result.skipped,
            'total_time_seconds': round(result.total_time, 2),
            'total_words_extracted': total_words,
            'total_chars_extracted': total_chars,
            'extraction_methods': methods_used
        },
        'errors': result.errors,
        'results': [
            {
                'input': r.input_path,
                'output': r.output_path,
                'success': r.success,
                'error': r.error_message,
                'time_seconds': round(r.conversion_time, 3),
                'input_size_bytes': r.input_size,
                'output_size_bytes': r.output_size,
                'quality': r.quality.to_dict() if r.quality else None
            }
            for r in result.results
        ]
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="""
KingAI Markdown Converter v1.2 - Batch document conversion

Converts PDF, DOCX, XLSX, PPTX, and more to Markdown.
Uses multiprocessing with graceful degradation for large runs.

Examples:
  python convert.py "./documents" --extensions pdf
  python convert.py "./source" "./documents" -o "out"
  python convert.py "./documents" -w 16 -e pdf docx
  python convert.py "./documents" --resume --safe-mode
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        'input_dirs',
        nargs='*',
        help='Input directories or files to convert'
    )
    
    parser.add_argument(
        '-o', '--output',
        help='Output directory for converted files (default: same as input)'
    )
    
    parser.add_argument(
        '-e', '--extensions',
        nargs='+',
        default=['pdf', 'docx'],
        help='File extensions to convert (default: pdf docx)'
    )
    
    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=None,
        help=f'Number of worker processes (default: auto, max {get_optimal_workers()})'
    )
    
    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='Overwrite existing converted files'
    )
    
    parser.add_argument(
        '--no-recursive',
        action='store_true',
        help='Do not scan subdirectories'
    )
    
    parser.add_argument(
        '--exclude',
        nargs='+',
        help='Patterns to exclude from conversion'
    )
    
    parser.add_argument(
        '--flat',
        action='store_true',
        help='Put all output files in a flat directory (no subdirectories)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be converted without actually converting'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )
    
    parser.add_argument(
        '--report',
        help='Save conversion report to JSON file'
    )
    
    parser.add_argument(
        '--list-extensions',
        action='store_true',
        help='List all supported file extensions'
    )
    
    # v1.1 flags
    parser.add_argument(
        '--batch-size',
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            f'Process files in batches of N '
            f'(default: {DEFAULT_BATCH_SIZE})'
        )
    )
    
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume from checkpoint if available'
    )
    
    parser.add_argument(
        '--log-file',
        help='Write progress to a log file'
    )
    
    parser.add_argument(
        '--safe-mode',
        action='store_true',
        help='Start with 2 workers, maximum caution'
    )

    parser.add_argument(
        '--file-timeout',
        type=int,
        default=DEFAULT_FILE_TIMEOUT_SECONDS,
        help=(
            'Maximum seconds to allow one file conversion before '
            f'timing out (default: {DEFAULT_FILE_TIMEOUT_SECONDS}; '
            'use 0 to disable)'
        )
    )
    
    args = parser.parse_args()
    
    # List extensions and exit
    if args.list_extensions:
        if RICH_AVAILABLE:
            tbl = Table(
                title="Supported File Extensions",
                show_header=True,
                header_style="bold cyan"
            )
            tbl.add_column("Extension", style="green")
            tbl.add_column("Description", style="white")
            for ext, desc in sorted(SUPPORTED_EXTENSIONS.items()):
                tbl.add_row(ext, desc)
            rconsole.print()
            rconsole.print(tbl)
            rconsole.print()
        else:
            print("\nSupported File Extensions:")
            print("=" * 40)
            for ext, desc in sorted(SUPPORTED_EXTENSIONS.items()):
                print(f"  {ext:8} - {desc}")
            print()
        return 0

    if not args.input_dirs:
        parser.error(
            "at least one input directory or file is required unless "
            "--list-extensions is used"
        )
    
    # Run batch conversion
    try:
        # Safe mode: override workers to 2
        workers = args.workers
        if args.safe_mode:
            workers = 2
            logger.info("Safe mode: using 2 workers")
        
        result = batch_convert(
            input_dirs=args.input_dirs,
            output_dir=args.output,
            extensions=args.extensions,
            recursive=not args.no_recursive,
            overwrite=args.overwrite,
            max_workers=workers,
            verbose=args.verbose,
            exclude_patterns=args.exclude,
            preserve_structure=not args.flat,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            resume=args.resume,
            log_file=args.log_file,
            file_timeout=args.file_timeout,
        )
        
        # Save report if requested
        if args.report:
            save_report(result, args.report)
        
        # Return appropriate exit code
        if result.failed > 0:
            return 1
        return 0
        
    except KeyboardInterrupt:
        logger.info("\nConversion interrupted by user")
        logger.info("Use --resume to continue from checkpoint")
        return 130
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
