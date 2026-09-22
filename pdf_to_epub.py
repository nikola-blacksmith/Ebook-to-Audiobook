"""
pdf_to_epub.py

Converts a PDF file to an ePub using a two-tier, fully local pipeline that
runs natively on Apple Silicon (no CUDA, no large model downloads):

  Tier 1 — PyMuPDF native text extraction (instant, works on digital PDFs)
  Tier 2 — Tesseract OCR (Homebrew, for scanned/image-only pages)

The pipeline:
  PDF  ──(PyMuPDF)──► Extract text per page
                         ├── Page has text?  ──► Use it directly
                         └── Page is blank?  ──► Render to image ──(Tesseract)──► OCR text
  All pages text ──(chapter detection)──► ePub (EbookLib)
"""

import os
import re
import gc
import io


# ---------------------------------------------------------------------------
# Tesseract availability check (optional, graceful fallback)
# ---------------------------------------------------------------------------

def _tesseract_available():
    """Return True if tesseract is installed and on PATH."""
    import shutil
    return shutil.which("tesseract") is not None


def _ocr_image_tesseract(pil_image):
    """
    Run Tesseract OCR on a PIL Image. Returns extracted text string.
    Raises RuntimeError if pytesseract or tesseract binary is not available.
    """
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError(
            "pytesseract not installed.\n"
            "Run: pip install pytesseract\n"
            "And install tesseract: brew install tesseract"
        )
    if not _tesseract_available():
        raise RuntimeError(
            "tesseract binary not found.\n"
            "Install it with: brew install tesseract"
        )
    return pytesseract.image_to_string(pil_image, lang="eng")


# ---------------------------------------------------------------------------
# PDF text extraction (Tier 1 + Tier 2 combined per page)
# ---------------------------------------------------------------------------

# Pages with fewer characters than this threshold are treated as image-only
# and sent to Tesseract for OCR.
_MIN_TEXT_CHARS = 50


def _extract_pages(pdf_path, dpi=200, progress_callback=None):
    """
    Extract text from every page of the PDF.

    For each page:
      - Try native PyMuPDF text extraction (Tier 1).
      - If the extracted text is too short (scanned page), render it to an
        image and run Tesseract (Tier 2).

    Returns a list of (page_num, text_str) tuples.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError(
            "PyMuPDF not installed.\nRun: pip install pymupdf"
        )

    doc = fitz.open(pdf_path)
    total = len(doc)
    print(f"  PDF has {total} pages.")

    has_tesseract = _tesseract_available()
    if not has_tesseract:
        print("  Note: tesseract not found — scanned pages will be skipped.")
        print("  To enable OCR for scanned pages: brew install tesseract && pip install pytesseract")

    results = []
    for page_num in range(total):
        page = doc[page_num]
        msg = f"Extracting page {page_num + 1}/{total}..."
        if progress_callback:
            progress_callback(msg)

        # --- Tier 1: native text extraction ---
        native_text = page.get_text("text").strip()

        if len(native_text) >= _MIN_TEXT_CHARS:
            results.append((page_num + 1, native_text))
            continue

        # --- Tier 2: Tesseract OCR for image-only pages ---
        if has_tesseract:
            print(f"  Page {page_num + 1}: minimal text detected, using Tesseract OCR...")
            try:
                from PIL import Image
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                pil_image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                ocr_text = _ocr_image_tesseract(pil_image)
                results.append((page_num + 1, ocr_text.strip()))
            except Exception as e:
                print(f"  Warning: OCR failed for page {page_num + 1}: {e}")
                results.append((page_num + 1, f"[Page {page_num + 1} could not be processed]"))
        else:
            # No tesseract — skip blank/scanned page
            if native_text:
                results.append((page_num + 1, native_text))
            else:
                print(f"  Page {page_num + 1}: skipped (scanned, no tesseract available).")

    doc.close()
    return results


# ---------------------------------------------------------------------------
# Chapter detection from page text
# ---------------------------------------------------------------------------

def _split_into_chapters(pages_text):
    """
    Given a list of (page_num, text) tuples, group them into chapters.

    A new chapter starts when:
      - A line that looks like a chapter heading is found at the top of a page, OR
      - Every MAX_PAGES_PER_CHAPTER pages (fallback grouping).

    Returns:
        List of dicts: [{"title": str, "content": str}, ...]
    """
    MAX_PAGES_PER_CHAPTER = 10

    # Matches common chapter heading patterns:
    #   "Chapter 1", "CHAPTER ONE", "1. Introduction", "Part II — The Beginning"
    CHAPTER_RE = re.compile(
        r"^(?:chapter|part|section|prologue|epilogue|introduction|conclusion)\b",
        re.IGNORECASE | re.MULTILINE
    )
    # Also match lines that are short (< 60 chars), title-cased, and near the top
    HEADING_RE = re.compile(r"^[A-Z][A-Za-z0-9 :\-]{3,55}$", re.MULTILINE)

    chapters = []
    current_title = "Introduction"
    current_pages = []

    for page_num, text in pages_text:
        first_lines = text.lstrip()[:200]
        is_chapter_start = bool(CHAPTER_RE.match(first_lines))

        # Use heading detection as secondary signal
        if not is_chapter_start:
            heading_match = HEADING_RE.match(first_lines)
            if heading_match and len(current_pages) > 0:
                is_chapter_start = True

        # Fallback: force a new chapter every MAX_PAGES_PER_CHAPTER pages
        if len(current_pages) >= MAX_PAGES_PER_CHAPTER:
            is_chapter_start = True

        if is_chapter_start and current_pages:
            chapters.append({
                "title": current_title,
                "content": "\n\n".join(p for _, p in current_pages)
            })
            current_pages = []
            # Extract a title from the first line of this page
            first_line = text.strip().splitlines()[0].strip() if text.strip() else f"Part {len(chapters) + 2}"
            current_title = first_line[:80]  # cap title length

        current_pages.append((page_num, text))

    # Flush last chapter
    if current_pages:
        chapters.append({
            "title": current_title,
            "content": "\n\n".join(p for _, p in current_pages)
        })

    return chapters


# ---------------------------------------------------------------------------
# ePub assembly
# ---------------------------------------------------------------------------

def _text_to_html(text):
    """Convert plain text to simple HTML paragraphs."""
    import html as html_mod
    lines = text.splitlines()
    parts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        escaped = html_mod.escape(line)
        parts.append(f"<p>{escaped}</p>")
    return "\n".join(parts)


def _assemble_epub(book_title, chapters, output_epub_path):
    """
    Build an ePub file from a list of chapter dicts using EbookLib.

    Args:
        book_title:       Title string for the book.
        chapters:         List of {"title": str, "content": str} dicts.
        output_epub_path: Where to save the .epub file.
    """
    try:
        from ebooklib import epub
    except ImportError:
        raise RuntimeError(
            "EbookLib not installed.\nRun: pip install EbookLib"
        )

    book = epub.EpubBook()
    book.set_title(book_title)
    book.set_language("en")
    book.add_author("Converted via PDF to ePub")

    spine = ["nav"]
    toc = []

    for idx, ch in enumerate(chapters, start=1):
        html_body = _text_to_html(ch["content"])
        import html as html_mod
        safe_title = html_mod.escape(ch["title"])

        epub_ch = epub.EpubHtml(
            title=ch["title"],
            file_name=f"chapter_{idx:03d}.xhtml",
            lang="en"
        )
        epub_ch.content = (
            f"<html><body>"
            f"<h1>{safe_title}</h1>"
            f"{html_body}"
            f"</body></html>"
        )
        book.add_item(epub_ch)
        spine.append(epub_ch)
        toc.append(epub.Link(f"chapter_{idx:03d}.xhtml", ch["title"], f"ch{idx}"))

    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(output_epub_path, book)
    print(f"  ePub written to: {output_epub_path}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_pdf_to_epub(pdf_path, output_epub_path=None, progress_callback=None):
    """
    Full pipeline: PDF → text extraction (+ OCR fallback) → ePub.

    Args:
        pdf_path:          Absolute path to the input PDF.
        output_epub_path:  Where to save the output .epub. If None, saves
                           alongside the PDF with the same base name.
        progress_callback: Optional callable(message: str) for UI status updates.

    Returns:
        Path to the generated .epub file.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Default output path: same directory as PDF, same stem, .epub extension
    if output_epub_path is None:
        base = os.path.splitext(pdf_path)[0]
        output_epub_path = base + ".epub"

    pdf_basename = os.path.basename(pdf_path)
    book_title = (
        os.path.splitext(pdf_basename)[0]
        .replace("_", " ")
        .replace("-", " ")
        .title()
    )

    print(f"\n{'='*60}")
    print(f"PDF → ePub Conversion: {pdf_basename}")
    print(f"{'='*60}")

    # --- Step 1: Extract text from all pages ---
    if progress_callback:
        progress_callback("Step 1/3: Extracting text from PDF pages...")
    pages_text = _extract_pages(pdf_path, dpi=200, progress_callback=progress_callback)

    if not pages_text:
        raise ValueError("No text could be extracted from the PDF.")

    # --- Step 2: Group pages into chapters ---
    if progress_callback:
        progress_callback("Step 2/3: Detecting chapters...")
    chapters = _split_into_chapters(pages_text)
    print(f"  Detected {len(chapters)} chapters/sections.")

    # --- Step 3: Build ePub ---
    if progress_callback:
        progress_callback("Step 3/3: Assembling ePub...")
    _assemble_epub(book_title, chapters, output_epub_path)

    print(f"\nConversion complete!\n  ePub: {output_epub_path}")
    return output_epub_path


# ---------------------------------------------------------------------------
# CLI entry point (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert a PDF to ePub (native text extraction + optional Tesseract OCR)."
    )
    parser.add_argument("pdf_file", help="Path to the input PDF file")
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Path for the output .epub (default: same dir as PDF)"
    )
    args = parser.parse_args()

    result = convert_pdf_to_epub(args.pdf_file, args.output)
    print(f"\nDone: {result}")
