from __future__ import annotations

from pathlib import Path


class RagExtractionError(ValueError):
    pass


def extract_document(data: bytes, filename: str, *, max_pages: int = 150, max_chars: int = 1_000_000) -> tuple[str, str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".md", ".pdf"}:
        raise RagExtractionError("RAG_UNSUPPORTED_SOURCE")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RagExtractionError("RAG_PDF_SUPPORT_UNAVAILABLE") from exc
        reader = PdfReader(__import__("io").BytesIO(data))
        if len(reader.pages) > max_pages:
            raise RagExtractionError("RAG_PDF_PAGE_LIMIT")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        parser_version = "pypdf-v1"
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RagExtractionError("RAG_INVALID_UTF8") from exc
        parser_version = "text-v1"
    text = text.strip()
    if not text:
        raise RagExtractionError("RAG_TEXT_EXTRACTION_EMPTY")
    if len(text) > max_chars:
        raise RagExtractionError("RAG_EXTRACTED_CHAR_LIMIT")
    return text, suffix[1:], parser_version
