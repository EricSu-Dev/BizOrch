"""Bounded local-file parsing for enterprise knowledge ingestion."""

import re
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class KnowledgeFileError(ValueError):
    """Base error for files that cannot safely become knowledge text."""


class UnsupportedKnowledgeFileError(KnowledgeFileError):
    """Raised when the file extension is outside the first-version allowlist."""


class KnowledgeFileTooLargeError(KnowledgeFileError):
    """Raised when raw bytes, extracted text, or PDF pages exceed limits."""


class KnowledgeFileDependencyError(KnowledgeFileError):
    """Raised when the configured parser dependency is unavailable."""


class ParsedKnowledgeFile(BaseModel):
    """Normalized parser output passed to the existing text ingestion pipeline."""

    model_config = ConfigDict(frozen=True)

    file_name: str
    file_format: str
    media_type: str
    text: str
    page_count: int | None
    byte_count: int
    content_digest: str


class KnowledgeFileParser:
    """Parse UTF-8 text, Markdown, and text-based PDF files within strict limits."""

    _TEXT_FORMATS = {
        ".txt": ("text", "text/plain"),
        ".md": ("markdown", "text/markdown"),
        ".markdown": ("markdown", "text/markdown"),
    }

    def __init__(
        self,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        max_characters: int = 2_000_000,
        max_pdf_pages: int = 200,
    ) -> None:
        if max_bytes < 1 or max_characters < 1 or max_pdf_pages < 1:
            raise ValueError("knowledge file limits must be positive")
        self.max_bytes = max_bytes
        self.max_characters = max_characters
        self.max_pdf_pages = max_pdf_pages

    def parse_path(self, path: Path) -> ParsedKnowledgeFile:
        """Read and parse one trusted local path without following directory input."""
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise KnowledgeFileError("knowledge path must be a regular file")
        byte_count = resolved.stat().st_size
        self._require_size(byte_count)
        return self.parse_bytes(resolved.name, resolved.read_bytes())

    def parse_bytes(self, file_name: str, content: bytes) -> ParsedKnowledgeFile:
        """Parse bytes selected by an allowlisted extension, never by claimed MIME."""
        safe_name = file_name.strip()
        if not safe_name or safe_name in {".", ".."}:
            raise KnowledgeFileError("knowledge file name must not be blank")
        if len(safe_name) > 255:
            raise KnowledgeFileError("knowledge file name is too long")
        if any(character in safe_name for character in ("/", "\\", "\x00")):
            raise KnowledgeFileError("knowledge file name must not contain a path")
        self._require_size(len(content))
        suffix = Path(safe_name).suffix.lower()
        page_count: int | None = None
        if suffix in self._TEXT_FORMATS:
            if content.lstrip().startswith(b"%PDF-"):
                raise KnowledgeFileError("knowledge file format does not match suffix")
            file_format, media_type = self._TEXT_FORMATS[suffix]
            text = self._decode_utf8(content)
        elif suffix == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise KnowledgeFileError("knowledge file format does not match suffix")
            file_format, media_type = "pdf", "application/pdf"
            text, page_count = self._parse_pdf(content)
        else:
            raise UnsupportedKnowledgeFileError(
                "supported knowledge files are .md, .markdown, .txt, and .pdf"
            )

        normalized = self._normalize_text(text)
        if not normalized:
            raise KnowledgeFileError("knowledge file does not contain extractable text")
        if len(normalized) > self.max_characters:
            raise KnowledgeFileTooLargeError(
                "extracted knowledge text exceeds the character limit"
            )
        return ParsedKnowledgeFile(
            file_name=safe_name,
            file_format=file_format,
            media_type=media_type,
            text=normalized,
            page_count=page_count,
            byte_count=len(content),
            content_digest=sha256(content).hexdigest(),
        )

    def _parse_pdf(self, content: bytes) -> tuple[str, int]:
        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover - dependency is version-locked
            raise KnowledgeFileDependencyError("PyMuPDF is not installed") from exc

        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    raise KnowledgeFileError("encrypted PDF files are not supported")
                if document.page_count > self.max_pdf_pages:
                    raise KnowledgeFileTooLargeError(
                        "PDF exceeds the configured page limit"
                    )
                pages = []
                for index, page in enumerate(document, start=1):
                    page_text = self._normalize_text(page.get_text("text"))
                    if page_text:
                        pages.append(f"[第 {index} 页]\n{page_text}")
                return "\n\n".join(pages), document.page_count
        except KnowledgeFileError:
            raise
        except Exception as exc:
            raise KnowledgeFileError("PDF file is invalid or cannot be parsed") from exc

    def _require_size(self, byte_count: int) -> None:
        if byte_count == 0:
            raise KnowledgeFileError("knowledge file must not be empty")
        if byte_count > self.max_bytes:
            raise KnowledgeFileTooLargeError(
                "knowledge file exceeds the configured byte limit"
            )

    @staticmethod
    def _decode_utf8(content: bytes) -> str:
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise KnowledgeFileError(
                "text knowledge files must use UTF-8 encoding"
            ) from exc

    @staticmethod
    def _normalize_text(text: str) -> str:
        if "\x00" in text:
            raise KnowledgeFileError("knowledge text contains null bytes")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[\t ]+", " ", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()
