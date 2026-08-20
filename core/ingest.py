"""
Ingest & Classify — detects format and extracts clean text from raw documents.

Supported inputs:
  - PDF (text-based)  → Docling → markdown
  - PDF (scanned)     → base64 image for VLM
  - HTML URL/file     → Trafilatura → clean text
  - CSV/XLSX          → Pandas → row dicts
  - Plain text        → pass-through
  - Image (PNG/JPG)   → base64 for VLM
"""
from __future__ import annotations
import base64
import mimetypes
from pathlib import Path
from dataclasses import dataclass
from enum import Enum


class InputType(str, Enum):
    TEXT = "text"
    PDF_TEXT = "pdf_text"
    PDF_IMAGE = "pdf_image"
    IMAGE = "image"
    HTML = "html"
    CSV = "csv"
    XLSX = "xlsx"
    UNKNOWN = "unknown"


@dataclass
class IngestedDocument:
    source_path: str
    input_type: InputType
    # Text content (for text-based inputs)
    text: str | None = None
    # Base64 image data (for image/scanned-pdf inputs)
    image_b64: list[str] | None = None
    image_media_type: str | None = None
    # For CSV/XLSX: list of row dicts
    rows: list[dict] | None = None
    # Short excerpt for display in review UI
    excerpt: str = ""


def detect_input_type(path: str | Path) -> InputType:
    path = Path(path)
    suffix = path.suffix.lower()
    mime, _ = mimetypes.guess_type(str(path))

    if suffix == ".pdf":
        return InputType.PDF_TEXT  # check further at parse time
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"):
        return InputType.IMAGE
    elif suffix in (".html", ".htm"):
        return InputType.HTML
    elif suffix == ".csv":
        return InputType.CSV
    elif suffix in (".xlsx", ".xls"):
        return InputType.XLSX
    elif suffix in (".txt", ".md"):
        return InputType.TEXT
    else:
        return InputType.UNKNOWN


def ingest_file(path: str | Path) -> IngestedDocument:
    """Main entry point — detect format and extract content."""
    path = Path(path)
    input_type = detect_input_type(path)

    if input_type == InputType.PDF_TEXT:
        return _ingest_pdf(path)
    elif input_type == InputType.IMAGE:
        return _ingest_image(path)
    elif input_type == InputType.HTML:
        return _ingest_html(path)
    elif input_type == InputType.CSV:
        return _ingest_csv(path)
    elif input_type == InputType.XLSX:
        return _ingest_xlsx(path)
    elif input_type == InputType.TEXT:
        return _ingest_text(path)
    else:
        # Try reading as text anyway
        return _ingest_text(path)


def ingest_text(text: str, source_name: str = "inline_text") -> IngestedDocument:
    """Ingest raw text directly (e.g. from UI paste)."""
    excerpt = text[:300].strip()
    return IngestedDocument(
        source_path=source_name,
        input_type=InputType.TEXT,
        text=text,
        excerpt=excerpt,
    )


def ingest_url(url: str) -> IngestedDocument:
    """Ingest an HTML page from a URL."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded) or ""
        excerpt = text[:300].strip()
        return IngestedDocument(
            source_path=url,
            input_type=InputType.HTML,
            text=text,
            excerpt=excerpt,
        )
    except ImportError:
        raise ImportError("pip install trafilatura")


# ── Private helpers ──────────────────────────────────────────────────────────

def _ingest_pdf(path: Path) -> IngestedDocument:
    """Try Docling first; fall back to image rendering for scanned PDFs."""
    try:
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(str(path))
        text = result.document.export_to_markdown()

        if len(text.strip()) < 50:
            # Likely scanned — render pages as images
            return _pdf_as_images(path)

        excerpt = text[:300].strip()
        return IngestedDocument(
            source_path=str(path),
            input_type=InputType.PDF_TEXT,
            text=text,
            excerpt=excerpt,
        )
    except ImportError:
        # Docling not installed — try basic text extraction
        return _pdf_fallback(path)


def _pdf_as_images(path: Path) -> IngestedDocument:
    """Render PDF pages as base64 PNG images for VLM processing."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        images_b64 = []
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            images_b64.append(base64.b64encode(img_bytes).decode())
        return IngestedDocument(
            source_path=str(path),
            input_type=InputType.PDF_IMAGE,
            image_b64=images_b64,
            image_media_type="image/png",
            excerpt=f"[Scanned PDF: {len(images_b64)} page(s)]",
        )
    except ImportError:
        raise ImportError("pip install PyMuPDF for scanned PDF support")


def _pdf_fallback(path: Path) -> IngestedDocument:
    """Simple text extraction without Docling."""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        excerpt = text[:300].strip()
        return IngestedDocument(
            source_path=str(path),
            input_type=InputType.PDF_TEXT,
            text=text,
            excerpt=excerpt,
        )
    except ImportError:
        # Last resort: read raw bytes and hope for the best
        text = path.read_text(errors="ignore")
        return IngestedDocument(
            source_path=str(path),
            input_type=InputType.TEXT,
            text=text,
            excerpt=text[:300].strip(),
        )


def _ingest_image(path: Path) -> IngestedDocument:
    suffix = path.suffix.lower().lstrip(".")
    media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "png": "image/png", "webp": "image/webp"}
    media_type = media_map.get(suffix, "image/png")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return IngestedDocument(
        source_path=str(path),
        input_type=InputType.IMAGE,
        image_b64=[b64],
        image_media_type=media_type,
        excerpt=f"[Image: {path.name}]",
    )


def _ingest_html(path: Path) -> IngestedDocument:
    try:
        import trafilatura
        html = path.read_text(encoding="utf-8", errors="ignore")
        text = trafilatura.extract(html) or html
    except ImportError:
        text = path.read_text(encoding="utf-8", errors="ignore")
    excerpt = text[:300].strip()
    return IngestedDocument(
        source_path=str(path),
        input_type=InputType.HTML,
        text=text,
        excerpt=excerpt,
    )


def _ingest_csv(path: Path) -> IngestedDocument:
    import pandas as pd
    df = pd.read_csv(path)
    rows = df.to_dict(orient="records")
    # Also build a text summary for the extractor
    text = df.to_markdown(index=False) if hasattr(df, "to_markdown") else df.to_string()
    return IngestedDocument(
        source_path=str(path),
        input_type=InputType.CSV,
        text=text,
        rows=rows,
        excerpt=text[:300].strip(),
    )


def _ingest_xlsx(path: Path) -> IngestedDocument:
    import pandas as pd
    df = pd.read_excel(path)
    rows = df.to_dict(orient="records")
    text = df.to_string()
    return IngestedDocument(
        source_path=str(path),
        input_type=InputType.XLSX,
        text=text,
        rows=rows,
        excerpt=text[:300].strip(),
    )


def _ingest_text(path: Path) -> IngestedDocument:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return IngestedDocument(
        source_path=str(path),
        input_type=InputType.TEXT,
        text=text,
        excerpt=text[:300].strip(),
    )
