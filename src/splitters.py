"""Chunking strategies, dispatched per document format.

Three strategies, because the evidence a chunk must support differs by source:

* **Markdown** — split by heading first, so a chunk knows it came from
  "Coding Standards › Error Handling" and can be cited that way.
* **Prose (PDF/DOCX)** — recursive character splitting on paragraph boundaries,
  page/heading metadata already attached by the loader.
* **Code** — ``RecursiveCharacterTextSplitter.from_language`` so chunks break on
  class/function boundaries instead of mid-statement.
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import (
    Language,
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .llm import get_settings

# Header levels captured as metadata by the Markdown splitter.
MARKDOWN_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]


def _prose_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
        add_start_index=True,
    )


def _heading_path(metadata: dict) -> str:
    """Join captured Markdown headers into a citable path."""
    parts = [metadata.get(key) for _, key in MARKDOWN_HEADERS]
    return " › ".join(part for part in parts if part)


def split_markdown(document: Document, chunk_size: int, chunk_overlap: int) -> list[Document]:
    """Split one Markdown document by heading, then by size within a section."""
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=MARKDOWN_HEADERS,
        strip_headers=False,
    )
    sections = header_splitter.split_text(document.page_content)

    # A document with no headings yields nothing useful from the header splitter.
    if not sections:
        sections = [Document(page_content=document.page_content, metadata={})]

    prepared: list[Document] = []
    for section in sections:
        heading = _heading_path(section.metadata)
        metadata = {**document.metadata}
        if heading:
            metadata["heading"] = heading
        prepared.append(Document(page_content=section.page_content, metadata=metadata))

    return _prose_splitter(chunk_size, chunk_overlap).split_documents(prepared)


def split_knowledge_documents(
    documents: list[Document],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Document]:
    """Split loaded knowledge documents, choosing a strategy per ``format``."""
    s = get_settings()
    size = chunk_size or s.chunk_size
    overlap = chunk_overlap if chunk_overlap is not None else s.chunk_overlap

    markdown = [d for d in documents if d.metadata.get("format") == "markdown"]
    prose = [d for d in documents if d.metadata.get("format") != "markdown"]

    chunks: list[Document] = []
    for document in markdown:
        chunks.extend(split_markdown(document, size, overlap))
    if prose:
        chunks.extend(_prose_splitter(size, overlap).split_documents(prose))
    return chunks


def _code_splitter(language: str, chunk_size: int, chunk_overlap: int):
    """Language-aware splitter, falling back to prose separators when unknown.

    ``getattr`` rather than a hard reference so an extension whose ``Language``
    member is missing from the installed ``langchain-text-splitters`` degrades
    gracefully instead of raising at import time.
    """
    member = getattr(Language, language, None) if language else None
    if member is None:
        return _prose_splitter(chunk_size, chunk_overlap)
    return RecursiveCharacterTextSplitter.from_language(
        language=member,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
    )


def split_code_documents(
    documents: list[Document],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Document]:
    """Split source files with a syntax-aware splitter, grouped by language."""
    s = get_settings()
    size = chunk_size or s.code_chunk_size
    overlap = chunk_overlap if chunk_overlap is not None else s.code_chunk_overlap

    by_language: dict[str, list[Document]] = {}
    for document in documents:
        by_language.setdefault(document.metadata.get("language", "text"), []).append(document)

    chunks: list[Document] = []
    for language, group in by_language.items():
        chunks.extend(_code_splitter(language, size, overlap).split_documents(group))
    return chunks


def split_review_documents(
    documents: list[Document],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Document]:
    """Split ingested review comments.

    Comments are short, so the only real job here is capping the occasional
    essay-length review without breaking the small ones apart.
    """
    s = get_settings()
    size = chunk_size or s.chunk_size
    overlap = chunk_overlap if chunk_overlap is not None else s.chunk_overlap
    return _prose_splitter(size, overlap).split_documents(documents)
