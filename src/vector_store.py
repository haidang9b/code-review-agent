"""Chroma vector stores: three collections in one persisted directory.

* ``knowledge``      — coding standards, ADRs, design docs (tiers 1–2)
* ``project_code``   — the local checkout's source (tier 3)
* ``review_history`` — past PR review comments (tier 4)

Everything persists to ``.chroma/`` (git-ignored). Chunk ids are deterministic,
so re-running an ingest upserts rather than duplicating; ``force=True`` drops the
collection first, which is what you want after deleting or renaming a document.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from langchain_chroma import Chroma
from langchain_core.documents import Document

from .config import (
    CHROMA_DIR,
    CODE_COLLECTION,
    KNOWLEDGE_COLLECTION,
    REVIEW_COLLECTION,
)
from .llm import get_embeddings

ALL_COLLECTIONS = (KNOWLEDGE_COLLECTION, CODE_COLLECTION, REVIEW_COLLECTION)

# Documents embedded per request. Keeps memory flat and gives usable progress
# output on a large repository.
BATCH_SIZE = 200


def get_store(collection: str) -> Chroma:
    """Open (creating if absent) a persisted Chroma collection."""
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=collection,
        embedding_function=get_embeddings(),
        persist_directory=str(CHROMA_DIR),
    )


def count_documents(collection: str) -> int:
    """Number of chunks currently stored in a collection."""
    store = get_store(collection)
    try:
        return store._collection.count()
    except AttributeError:  # pragma: no cover - depends on chromadb internals
        return len(store.get(include=[]).get("ids", []))


def collection_counts() -> dict[str, int]:
    """Chunk count per collection, for the ``status`` command."""
    return {name: count_documents(name) for name in ALL_COLLECTIONS}


def clear_collection(collection: str) -> None:
    """Delete every chunk in a collection, leaving it empty but usable."""
    get_store(collection).delete_collection()


def _sanitize(metadata: dict) -> dict:
    """Chroma accepts only scalar metadata values; drop empties, stringify rest."""
    clean: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None or value == "":
            continue
        clean[key] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return clean


def _chunk_id(document: Document) -> str:
    """Stable id from origin plus content, so re-ingest upserts in place.

    Content is part of the hash on purpose: an edited rule produces a new id and
    the stale chunk is removed by the next ``--force`` rebuild rather than
    silently shadowing the update.
    """
    metadata = document.metadata
    key = "|".join(
        str(metadata.get(field, ""))
        for field in ("source", "page", "heading", "start_index")
    )
    digest = hashlib.sha1(
        f"{key}|{document.page_content}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return f"{metadata.get('source', 'unknown')}:{digest[:16]}"


def _batched(items: list[Document], size: int) -> Iterable[list[Document]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def index_documents(
    collection: str,
    chunks: list[Document],
    force: bool = False,
    progress: bool = True,
) -> int:
    """Embed and persist chunks into a collection, returning how many were written.

    Args:
        collection: target collection name.
        chunks: pre-split documents to embed.
        force: drop the collection before writing, discarding chunks whose
            source document no longer exists.
        progress: print per-batch progress (the CLI wants it, tests do not).
    """
    if force:
        clear_collection(collection)

    if not chunks:
        return 0

    store = get_store(collection)
    written = 0
    for batch in _batched(chunks, BATCH_SIZE):
        prepared = [
            Document(page_content=d.page_content, metadata=_sanitize(d.metadata))
            for d in batch
        ]
        store.add_documents(prepared, ids=[_chunk_id(d) for d in batch])
        written += len(batch)
        if progress:
            print(f"  embedded {written}/{len(chunks)} chunks", flush=True)

    return written
