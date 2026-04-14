"""Extract searchable plain text from in-memory bytes (GCS downloads, uploads).

Rules align with ``dossier_analyzer.scan.SUPPORTED_EXTENSIONS``: PDF and Markdown body text;
images contribute the filename only (no OCR).
"""

from __future__ import annotations

import fitz  # PyMuPDF

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"})


def _read_pdf_bytes(data: bytes) -> str:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return ""
    parts: list[str] = []
    try:
        for page in doc:
            parts.append(page.get_text() or "")
    finally:
        doc.close()
    return "\n".join(parts)


def extract_text_from_bytes(data: bytes, suffix: str, filename: str = "") -> str:
    """Decode supported file types to plain text for keyword indexing."""
    suf = (suffix or "").lower()
    if not data:
        return ""
    if suf == ".pdf":
        return _read_pdf_bytes(data)
    if suf in {".md", ".markdown"}:
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if suf in _IMAGE_SUFFIXES:
        name = filename or "image"
        return f"{name}\n"
    return ""
