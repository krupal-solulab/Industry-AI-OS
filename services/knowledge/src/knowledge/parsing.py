"""Document parsing + chunking.

Docling does layout-aware parsing (PDF/DOCX/PPTX/HTML) into clean text; if Docling
is unavailable or fails (e.g. a plain .txt), we fall back to UTF-8 decoding. Chunking
uses LlamaIndex's SentenceSplitter. Both tools sit behind this module so either can
be swapped (Docling -> Unstructured, LlamaIndex splitter -> another) without touching
the ingestion flow.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import structlog

log = structlog.get_logger("aios.knowledge.parsing")


def parse_to_text(filename: str, data: bytes) -> str:
    """Return plain text for a document, using Docling when possible."""
    try:
        from docling.document_converter import DocumentConverter

        suffix = Path(filename).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    except Exception as exc:
        log.info("docling.fallback", filename=filename, reason=str(exc))
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return ""


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks for embedding."""
    text = text.strip()
    if not text:
        return []
    try:
        from llama_index.core.node_parser import SentenceSplitter

        splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
        return [c for c in splitter.split_text(text) if c.strip()]
    except Exception as exc:
        log.info("splitter.fallback", reason=str(exc))
        # Simple character-window fallback.
        step = chunk_size - overlap
        return [text[i : i + chunk_size] for i in range(0, len(text), step)]
