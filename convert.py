#!/usr/bin/env python3
"""
KingAI Markdown Converter v1.1
==============================
Batch document converter with quality metrics, multi-library fallback,
and graceful degradation for large-scale document processing.

v1.1 adds:
- Pre-flight file validation (magic bytes, min size)
- Exponential backoff with Full Jitter on extractor failures
- Batch chunking with checkpoints for crash recovery
- File-based progress logging (survives terminal crashes)
- --resume flag to continue from where you left off
- Error classification (invalid_file, corrupt, timeout, etc.)
- Adaptive worker scaling under memory pressure
Will be adding 

will be making a seperate script for testing different ocr and image to text scripts in a monte carlo method to see what handles what
then will make a frontend (pyside6 qt5/6 and pyqt based) and cli version which matchs the functions of that. with confidence and more quality of life and testing sandbox 
for image based pdfs to text like how algerbra is read. this will require a seperate github, maybe testers and contributers. i cant tests all edge cases. only on stuff i have access to.
multiple outputs to compare what works better for what sort of diagrams. what needs manual checking even on the best method.  



Author: Jason King (KingAI Pty Ltd)
License: MIT
Version: 1.1.0
"""

import argparse
import gc
import json
import logging
import os
import random
import re
import sys
import time
import hashlib
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
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
        TaskProgressColumn,
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
__version__ = "1.1.0"

# Supported file extensions
SUPPORTED_EXTENSIONS = {
    '.pdf': 'PDF Document',
    '.docx': 'Word Document',
    '.doc': 'Legacy Word Document',
    '.xlsx': 'Excel Spreadsheet',
    '.xls': 'Legacy Excel Spreadsheet',
    '.pptx': 'PowerPoint Presentation',
    '.ppt': 'Legacy PowerPoint',
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
# Minimum file sizes (bytes) — files below this can't be valid
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
        return result.markdown, "markitdown"
    except ImportError:
        raise
    except Exception as e:
        raise RuntimeError(f"MarkItDown failed: {e}")


def extract_with_pymupdf4llm(file_path: Path) -> Tuple[str, str]:
    """Extract using PyMuPDF4LLM (optimized for LLM/RAG).
    
    If pymupdf-layout is installed, activates its ONNX-based document
    layout analyzer for improved page structure detection. The layout
    package must be imported BEFORE pymupdf4llm to activate — pymupdf
    does not auto-import its own subpackage.
    """
    try:
        # Activate ONNX layout analyzer if available (must happen before pymupdf4llm import)
        try:
            import pymupdf.layout  # noqa: F401 — side-effect: sets pymupdf._get_layout
        except ImportError:
            pass  # pymupdf-layout not installed — uses standard layout analysis
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
            import pymupdf.layout  # noqa: F401 — suppress layout warning
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
        raise ImportError("No PDF extraction library installed")
    
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
            # Use best PDF extraction with fallback
            markdown_text, quality = convert_pdf_with_best_quality(
                input_path, verbose
            )
        else:
            # Use MarkItDown for other formats
            from markitdown import MarkItDown
            md = MarkItDown()
            result = md.convert(str(input_path))
            markdown_text = result.markdown
            quality = calculate_quality_metrics(markdown_text, "markitdown")
        
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
        _ALWAYS_SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv',
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
        
        # Skip if already done (resume mode)
        if str(doc_path) in completed_paths_set:
            result.skipped += 1
            continue
        
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
    num_batches = ceil(total_tasks / batch_size)
    
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
    
    for batch_num in range(num_batches):
        batch_start = batch_num * batch_size
        batch_end = min(batch_start + batch_size, total_tasks)
        batch = tasks[batch_start:batch_end]
        
        if num_batches > 1:
            logger.info(
                f"Batch {batch_num + 1}/{num_batches}: "
                f"files {batch_start + 1}-{batch_end} "
                f"of {total_tasks}"
            )
        
        # Adjust workers based on failure rate
        adj_workers = worker_pool.get_adjusted_workers()
        
        with ProcessPoolExecutor(
            max_workers=adj_workers
        ) as executor:
            futures = {
                executor.submit(convert_single_file, task): task
                for task in batch
            }
            
            for future in as_completed(futures):
                completed_count += 1
                try:
                    conv_result = future.result(timeout=300)
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
                                f"  [green]\u2713[/green] "
                                f"[white]{status}[/white] "
                                f"[dim]{detail.strip()}[/dim]"
                            )
                        elif not conv_result.success:
                            rconsole.print(
                                f"  [red]\u2717[/red] "
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
                    result.failed += 1
                    worker_pool.record_result(False)
                    task = futures[future]
                    failed_dict[str(task[0])] = "Timeout (>5min)"
                    result.errors.append(
                        f"[timeout] {task[0]}: Timeout (>5min)"
                    )
                    if use_rich_progress and rich_progress:
                        rich_progress.advance(rich_task)
                        rconsole.print(
                            f"  [yellow]\u23f0[/yellow] "
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
                            f"  [red]\u2717[/red] "
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
        if PSUTIL_AVAILABLE and num_batches > 1:
            mem = psutil.virtual_memory()
            logger.info(
                f"Memory: {mem.percent}% used "
                f"({mem.available / 1_073_741_824:.1f}GB free)"
            )
    
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
KingAI Markdown Converter v1.1 - Batch document conversion

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
        nargs='+',
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
            print("\n📄 Supported File Extensions:")
            print("=" * 40)
            for ext, desc in sorted(SUPPORTED_EXTENSIONS.items()):
                print(f"  {ext:8} - {desc}")
            print()
        return 0
    
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
