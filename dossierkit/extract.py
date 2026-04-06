"""Extract searchable plain text from PDF, Markdown, and image paths (filename only for images)."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from dossierkit.scan import SUPPORTED_EXTENSIONS, TreeNode


def _read_pdf_text(path: Path) -> str:
    try:
        doc = fitz.open(path)
    except Exception:
        return ""
    parts: list[str] = []
    try:
        for page in doc:
            parts.append(page.get_text() or "")
    finally:
        doc.close()
    return "\n".join(parts)


def _read_markdown(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"})


def extract_text_for_path(path: Path) -> str:
    path = path.resolve()
    if not path.is_file():
        return ""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf_text(path)
    if suffix in {".md", ".markdown"}:
        return _read_markdown(path)
    if suffix in _IMAGE_SUFFIXES:
        # No OCR in the basic version; still expose filename for naive keyword hits.
        return f"{path.name}\n"
    return ""


def collect_all_file_paths(node: TreeNode) -> list[Path]:
    paths: list[Path] = list(node.files)
    for c in node.children:
        paths.extend(collect_all_file_paths(c))
    return paths


def aggregate_folder_text(node: TreeNode) -> str:
    """Concatenate extracted text from every file in this folder's subtree."""
    chunks: list[str] = []
    for f in collect_all_file_paths(node):
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        chunks.append(extract_text_for_path(f))
    return "\n".join(chunks)
