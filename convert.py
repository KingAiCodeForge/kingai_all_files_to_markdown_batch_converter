#!/usr/bin/env python3
"""
KingAI Markdown Converter v2.0
==============================
High-performance batch document converter with quality metrics and multi-library fallback.
Converts PDF, DOCX, XLSX, PPTX, and more to Markdown with multiprocessing support.

Features:
- Multiple extraction backends: MarkItDown, PyMuPDF4LLM, pdfplumber
- Quality metrics: character count, word count, line count, extraction ratio
- Similarity detection between conversion methods
- Optimized for i9-9900K (8 cores / 16 threads)

Author: Jason King (KingAI Pty Ltd)
License: MIT
Version: 2.0.0
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import multiprocessing

# Try to import tqdm for progress bars
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Version info
__version__ = "2.0.0"

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
    """Extract using PyMuPDF4LLM (optimized for LLM/RAG)"""
    try:
        import pymupdf4llm
        md_text = pymupdf4llm.to_markdown(str(file_path))
        return md_text, "pymupdf4llm"
    except ImportError:
        raise
    except Exception as e:
        raise RuntimeError(f"PyMuPDF4LLM failed: {e}")


def extract_with_pymupdf(file_path: Path) -> Tuple[str, str]:
    """Extract using PyMuPDF directly"""
    try:
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
    Best = highest word count (most complete extraction)
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
    
    for name, extractor in extractors:
        try:
            text, method = extractor(file_path)
            metrics = calculate_quality_metrics(text, method)
            results.append((text, metrics))
            if verbose:
                logger.debug(f"  {name}: {metrics.word_count} words")
        except ImportError:
            continue  # Library not installed
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
    
    if not results:
        if errors:
            raise RuntimeError(f"All extractors failed: {'; '.join(errors)}")
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
        
        # Find files
        pattern = "**/*" if recursive else "*"
        for file_path in root_path.glob(pattern):
            if not file_path.is_file():
                continue
            
            # Check extension
            if file_path.suffix.lower() not in extensions:
                continue
            
            # Check exclusions
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
    dry_run: bool = False
) -> BatchResult:
    """
    Convert multiple documents to markdown using multiprocessing.
    """
    start_time = time.time()
    result = BatchResult()
    
    # Find all documents
    logger.info("🔍 Scanning for documents...")
    documents = find_documents(input_dirs, extensions, recursive, exclude_patterns)
    result.total_files = len(documents)
    
    if not documents:
        logger.info("No documents found to convert.")
        return result
    
    logger.info(f"📁 Found {len(documents)} documents to process")
    
    # Prepare output directory
    output_path = Path(output_dir) if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)
    
    # Determine base directory for structure preservation
    base_dir = None
    if preserve_structure and len(input_dirs) == 1:
        base_dir = Path(input_dirs[0])
    
    # Prepare conversion tasks
    tasks = []
    for doc_path in documents:
        out_path = get_output_path(doc_path, output_path, preserve_structure, base_dir)
        
        # Skip if output exists and not overwriting
        if out_path.exists() and not overwrite:
            result.skipped += 1
            if verbose:
                logger.info(f"⏭️  Skipped (exists): {doc_path.name}")
            continue
        
        tasks.append((doc_path, out_path, overwrite, verbose))
    
    if not tasks:
        logger.info("All files already converted (use --overwrite to reconvert)")
        result.total_time = time.time() - start_time
        return result
    
    if dry_run:
        logger.info(f"🔍 DRY RUN: Would convert {len(tasks)} files")
        for doc_path, out_path, _, _ in tasks[:10]:
            logger.info(f"   {doc_path.name} → {out_path.name}")
        if len(tasks) > 10:
            logger.info(f"   ... and {len(tasks) - 10} more")
        result.total_time = time.time() - start_time
        return result
    
    # Get optimal worker count
    num_workers = get_optimal_workers(max_workers)
    logger.info(f"🚀 Converting {len(tasks)} files with {num_workers} workers...")
    print()  # Blank line for live output
    
    # Process files with multiprocessing
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(convert_single_file, task): task for task in tasks}
        
        completed = 0
        total_tasks = len(tasks)
        
        # Create progress bar if tqdm is available
        if TQDM_AVAILABLE:
            pbar = tqdm(
                total=total_tasks,
                desc="Converting",
                unit="file",
                bar_format="{l_bar}{bar:30}{r_bar}",
                ncols=100,
                colour="green"
            )
        
        for future in as_completed(futures):
            completed += 1
            try:
                conv_result = future.result(timeout=300)  # 5 min timeout per file
                result.results.append(conv_result)
                
                if conv_result.success:
                    if "Skipped" in conv_result.error_message:
                        result.skipped += 1
                        status = f"⏭️  SKIP: {Path(conv_result.input_path).name}"
                    else:
                        result.successful += 1
                        q = conv_result.quality
                        words = q.word_count if q else 0
                        method = q.extraction_method if q else "unknown"
                        status = f"✅ {Path(conv_result.input_path).name}"
                        detail = f"   → {words:,} words | {conv_result.conversion_time:.1f}s | {method}"
                else:
                    result.failed += 1
                    result.errors.append(f"{conv_result.input_path}: {conv_result.error_message}")
                    status = f"❌ FAIL: {Path(conv_result.input_path).name}"
                    detail = f"   → {conv_result.error_message[:80]}"
                
                # Update progress bar or print status
                if TQDM_AVAILABLE:
                    pbar.update(1)
                    # Show file info below progress bar
                    tqdm.write(f"[{completed}/{total_tasks}] {status}")
                    if conv_result.success and "Skipped" not in conv_result.error_message:
                        tqdm.write(detail)
                    elif not conv_result.success:
                        tqdm.write(detail)
                else:
                    # Fallback to regular print with percentage
                    pct = (completed / total_tasks) * 100
                    print(f"[{pct:5.1f}%] [{completed}/{total_tasks}] {status}")
                    if conv_result.success and "Skipped" not in conv_result.error_message:
                        print(detail)
                    elif not conv_result.success:
                        print(detail)
                
                sys.stdout.flush()
                    
            except TimeoutError:
                result.failed += 1
                task = futures[future]
                error_msg = f"{task[0]}: Timeout (>5min)"
                result.errors.append(error_msg)
                if TQDM_AVAILABLE:
                    pbar.update(1)
                    tqdm.write(f"⏰ [{completed}/{total_tasks}] TIMEOUT: {Path(task[0]).name}")
                else:
                    pct = (completed / total_tasks) * 100
                    print(f"[{pct:5.1f}%] ⏰ [{completed}/{total_tasks}] TIMEOUT: {Path(task[0]).name}")
                sys.stdout.flush()
            except Exception as e:
                result.failed += 1
                task = futures[future]
                error_msg = f"{task[0]}: {e}"
                result.errors.append(error_msg)
                if TQDM_AVAILABLE:
                    pbar.update(1)
                    tqdm.write(f"❌ [{completed}/{total_tasks}] ERROR: {Path(task[0]).name}: {e}")
                else:
                    pct = (completed / total_tasks) * 100
                    print(f"[{pct:5.1f}%] ❌ [{completed}/{total_tasks}] ERROR: {Path(task[0]).name}: {e}")
                sys.stdout.flush()
        
        # Close progress bar
        if TQDM_AVAILABLE:
            pbar.close()
    
    result.total_time = time.time() - start_time
    
    # Summary
    logger.info("=" * 60)
    logger.info("📊 CONVERSION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"   Total files:  {result.total_files}")
    logger.info(f"   ✅ Successful: {result.successful}")
    logger.info(f"   ⏭️  Skipped:    {result.skipped}")
    logger.info(f"   ❌ Failed:     {result.failed}")
    logger.info(f"   ⏱️  Total time: {result.total_time:.2f}s")
    
    if result.successful > 0:
        avg_time = result.total_time / result.successful
        logger.info(f"   📈 Avg time:   {avg_time:.2f}s per file")
    
    return result


def save_report(result: BatchResult, output_path: str):
    """Save conversion report to JSON file with quality metrics."""
    
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
    
    logger.info(f"📝 Report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="""
KingAI Markdown Converter - High-performance batch document conversion

Converts PDF, DOCX, XLSX, PPTX, and more to Markdown using multiprocessing.
Optimized for high-core-count CPUs like i9-9900K.

Examples:
  # Convert all PDFs in E:\\Downloads
  python convert.py "E:\\Downloads" --extensions pdf
  
  # Convert all documents in multiple folders
  python convert.py "A:\\repos" "E:\\Downloads" --output "A:\\repos\\converted_docs"
  
  # Use maximum workers for fastest conversion
  python convert.py "E:\\Downloads" --workers 16 --extensions pdf docx
  
  # Dry run to see what would be converted
  python convert.py "A:\\repos" --dry-run
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
    
    args = parser.parse_args()
    
    # List extensions and exit
    if args.list_extensions:
        print("\n📄 Supported File Extensions:")
        print("=" * 40)
        for ext, desc in sorted(SUPPORTED_EXTENSIONS.items()):
            print(f"  {ext:8} - {desc}")
        print()
        return 0
    
    # Run batch conversion
    try:
        result = batch_convert(
            input_dirs=args.input_dirs,
            output_dir=args.output,
            extensions=args.extensions,
            recursive=not args.no_recursive,
            overwrite=args.overwrite,
            max_workers=args.workers,
            verbose=args.verbose,
            exclude_patterns=args.exclude,
            preserve_structure=not args.flat,
            dry_run=args.dry_run
        )
        
        # Save report if requested
        if args.report:
            save_report(result, args.report)
        
        # Return appropriate exit code
        if result.failed > 0:
            return 1
        return 0
        
    except KeyboardInterrupt:
        logger.info("\n⚠️  Conversion interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
