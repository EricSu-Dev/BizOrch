import pymupdf
import pytest

from app.knowledge.files import (
    KnowledgeFileError,
    KnowledgeFileParser,
    KnowledgeFileTooLargeError,
    UnsupportedKnowledgeFileError,
)


def test_markdown_parser_normalizes_utf8_bom_and_whitespace() -> None:
    parsed = KnowledgeFileParser().parse_bytes(
        "policy.md",
        "\ufeff# 访问制度\r\n\r\nVPN\t访问需要审批。".encode("utf-8"),
    )

    assert parsed.file_format == "markdown"
    assert parsed.media_type == "text/markdown"
    assert parsed.page_count is None
    assert parsed.text == "# 访问制度\n\nVPN 访问需要审批。"
    assert len(parsed.content_digest) == 64


def test_pdf_parser_extracts_page_markers_and_text() -> None:
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 72), "VPN remote access requires manager approval.")
    second = document.new_page()
    second.insert_text((72, 72), "Temporary access expires after 30 days.")
    content = document.tobytes()
    document.close()

    parsed = KnowledgeFileParser().parse_bytes("remote-access.pdf", content)

    assert parsed.file_format == "pdf"
    assert parsed.media_type == "application/pdf"
    assert parsed.page_count == 2
    assert "[第 1 页]" in parsed.text
    assert "manager approval" in parsed.text
    assert "[第 2 页]" in parsed.text
    assert "30 days" in parsed.text


def test_parser_rejects_unsupported_or_unbounded_files() -> None:
    parser = KnowledgeFileParser(max_bytes=8)

    with pytest.raises(UnsupportedKnowledgeFileError):
        parser.parse_bytes("policy.docx", b"content")
    with pytest.raises(KnowledgeFileTooLargeError):
        parser.parse_bytes("policy.txt", b"too many bytes")
    with pytest.raises(KnowledgeFileError):
        KnowledgeFileParser().parse_bytes("policy.txt", b"\xff\xfe")


@pytest.mark.parametrize(
    "file_name",
    ["../policy.md", "..\\policy.md", "a" * 252 + ".txt"],
)
def test_parser_rejects_path_like_or_overlong_file_names(file_name: str) -> None:
    with pytest.raises(KnowledgeFileError):
        KnowledgeFileParser().parse_bytes(file_name, b"policy")


def test_parser_rejects_extension_and_content_mismatch() -> None:
    parser = KnowledgeFileParser()

    with pytest.raises(KnowledgeFileError):
        parser.parse_bytes("fake.txt", b"%PDF-1.7\n")
    with pytest.raises(KnowledgeFileError):
        parser.parse_bytes("fake.pdf", b"plain text")
