"""Tiered retrieval: the mechanism behind the prompt's context-priority rule.

The reviewer prompt mandates a strict ordering of context sources. That is
implemented here rather than merely requested: each priority tier is retrieved
separately, with its own budget, and rendered under an explicit tier heading so
the model can tell a mandatory standard apart from an incidental code sample.

Query embeddings are computed once per file and reused across all three
collections (``similarity_search_by_vector``), which keeps a multi-probe search
to a single embedding request instead of one per query per collection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from langchain_core.documents import Document

from .config import (
    CODE_COLLECTION,
    KNOWLEDGE_COLLECTION,
    REVIEW_COLLECTION,
    TIER_ARCHITECTURE,
    TIER_CODING_STANDARD,
    TIER_LABELS,
    TIER_PREVIOUS_REVIEW,
    TIER_PROJECT_PATTERN,
)
from .llm import get_embeddings, get_settings
from .vector_store import get_store

# Fixed probes covering the 15 review responsibilities. They exist so a standard
# the diff never mentions by name (secrets handling, N+1 queries, timeouts) is
# still pulled in when relevant.
CONCERN_QUERIES = [
    "naming conventions for classes, methods and variables",
    "error handling, exceptions, and logging rules",
    "input validation, secrets handling and security requirements",
    "async, concurrency, locking and timeout rules",
    "layering, dependency direction and architecture compliance",
    "SOLID principles, design patterns and refactoring expectations",
    "database access, queries, caching and performance rules",
    "memory, allocation, streaming and algorithmic cost rules",
    "magic values, hardcoded literals and named constants",
    "function length, complexity and clean code limits",
    "unit test requirements, coverage and testability",
    "public API contracts, response shapes and versioning",
    "documentation and comment requirements",
]

# Results pulled per individual query before deduplication and tier capping.
PER_QUERY_K = 3

# How many changed symbols become their own retrieval query.
MAX_SYMBOL_QUERIES = 4

_TIER_COLLECTIONS = {
    TIER_CODING_STANDARD: KNOWLEDGE_COLLECTION,
    TIER_ARCHITECTURE: KNOWLEDGE_COLLECTION,
    TIER_PROJECT_PATTERN: CODE_COLLECTION,
    TIER_PREVIOUS_REVIEW: REVIEW_COLLECTION,
}

ALL_TIERS = (
    TIER_CODING_STANDARD,
    TIER_ARCHITECTURE,
    TIER_PROJECT_PATTERN,
    TIER_PREVIOUS_REVIEW,
)


def build_queries(path: str, language: str, symbols: list[str]) -> tuple[list[str], list[str]]:
    """Build the retrieval probes for one changed file, in two groups.

    Returns ``(specific, concerns)``. They are kept apart because which group
    should win a contested slot depends on the tier: a documented standard is
    found by asking about the *concern* ("what are our error handling rules"),
    while an existing implementation is found by asking about the *file and its
    symbols*. Merging them into one ordered list makes one of those two cases
    retrieve badly — which is how the "Magic Values" standard went missing while
    the file's own name kept winning slots.
    """
    specific = [f"{language} conventions for {path}"]
    specific.extend(
        f"{symbol} implementation and conventions" for symbol in symbols[:MAX_SYMBOL_QUERIES]
    )
    return specific, list(CONCERN_QUERIES)


# Which probe group leads for each tier. Standards and architecture rules are
# concern-shaped; existing code and past review decisions are file-shaped.
_CONCERN_LED_TIERS = (TIER_CODING_STANDARD, TIER_ARCHITECTURE)


def citation_for(document: Document) -> str:
    """Human-readable, verifiable pointer to where a chunk came from."""
    metadata = document.metadata
    source = metadata.get("source", "unknown")
    heading = metadata.get("heading")
    page = metadata.get("page")

    if heading:
        return f"{source} › {heading}"
    if page:
        return f"{source}, page {page}"
    return str(source)


def _fingerprint(document: Document) -> str:
    return hashlib.sha1(
        document.page_content.encode("utf-8"), usedforsecurity=False
    ).hexdigest()


@dataclass
class RetrievedContext:
    """Chunks retrieved for one file, grouped by priority tier."""

    by_tier: dict[int, list[Document]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not any(self.by_tier.values())

    def documents(self) -> list[Document]:
        """Every retrieved chunk, in tier order."""
        return [d for tier in ALL_TIERS for d in self.by_tier.get(tier, [])]

    def format(self) -> str:
        """Render as a tier-labelled context block for the review prompt."""
        if self.is_empty:
            return (
                "(no project documentation, source code or review history was "
                "retrieved for this file — no project-specific rule can be "
                "verified from context)"
            )

        blocks: list[str] = []
        for tier in ALL_TIERS:
            documents = self.by_tier.get(tier, [])
            if not documents:
                continue
            blocks.append(f"### {TIER_LABELS[tier]}")
            for document in documents:
                blocks.append(f"[{citation_for(document)}]\n{document.page_content}")
        return "\n\n".join(blocks)

    def grounding_corpus(self) -> str:
        """Raw concatenation of retrieved text, used by the verification gate."""
        return "\n\n---\n\n".join(
            f"[{citation_for(d)}]\n{d.page_content}" for d in self.documents()
        )

    def sources(self) -> list[str]:
        """Distinct citations consulted, for the report's evidence manifest."""
        seen: dict[str, None] = {}
        for document in self.documents():
            seen.setdefault(citation_for(document), None)
        return list(seen)

    def document_sources(self) -> list[str]:
        """Citations from tiers 1–2 only, i.e. actual project documentation.

        Distinct from :meth:`sources` because a review that retrieved plenty of
        source code but no standards has nothing to cite as a *rule*, and the
        confidence score needs to know the difference.
        """
        seen: dict[str, None] = {}
        for tier in (TIER_CODING_STANDARD, TIER_ARCHITECTURE):
            for document in self.by_tier.get(tier, []):
                seen.setdefault(citation_for(document), None)
        return list(seen)


def _search_tier(tier: int, vectors: list[list[float]], limit: int) -> list[Document]:
    """Retrieve up to ``limit`` distinct chunks for one tier.

    Results are merged **breadth-first**: every query contributes its best hit
    before any query contributes a second. Draining each query in turn instead
    would let the first one or two probes consume the whole tier budget, and the
    fixed concern probes — which exist precisely to surface a standard the diff
    never mentions by name — would never get a slot.

    Query order still breaks ties, so a file-specific match outranks a generic
    concern probe at the same depth.
    """
    if limit <= 0:
        return []

    store = get_store(_TIER_COLLECTIONS[tier])
    # Tiers 1 and 2 share the ``knowledge`` collection and must be separated by
    # metadata; the code and review collections need no filter.
    metadata_filter = (
        {"tier": tier}
        if _TIER_COLLECTIONS[tier] == KNOWLEDGE_COLLECTION
        else None
    )

    per_query: list[list[Document]] = []
    for vector in vectors:
        try:
            per_query.append(
                store.similarity_search_by_vector(
                    embedding=vector, k=PER_QUERY_K, filter=metadata_filter
                )
            )
        except Exception:  # noqa: BLE001 - an empty/missing collection must not abort review
            break

    picked: list[Document] = []
    seen: set[str] = set()
    for depth in range(PER_QUERY_K):
        for hits in per_query:
            if depth >= len(hits):
                continue
            key = _fingerprint(hits[depth])
            if key in seen:
                continue
            seen.add(key)
            picked.append(hits[depth])
            if len(picked) >= limit:
                return picked

    return picked


def retrieve_for_file(
    path: str,
    language: str,
    symbols: list[str] | None = None,
    tiers: tuple[int, ...] = ALL_TIERS,
) -> RetrievedContext:
    """Retrieve tier-grouped context for one changed file."""
    s = get_settings()
    specific, concerns = build_queries(path, language, symbols or [])

    # One batched embedding request for every probe; the per-tier searches below
    # are local vector lookups that reuse these vectors.
    embedded = get_embeddings().embed_documents(specific + concerns)
    specific_vectors = embedded[: len(specific)]
    concern_vectors = embedded[len(specific) :]

    by_tier = {}
    for tier in tiers:
        vectors = (
            concern_vectors + specific_vectors
            if tier in _CONCERN_LED_TIERS
            else specific_vectors + concern_vectors
        )
        by_tier[tier] = _search_tier(tier, vectors, s.tier_k(tier))

    return RetrievedContext(by_tier=by_tier)


def search(query: str, tier: int, k: int = 4) -> list[Document]:
    """Ad-hoc single-tier search. Useful for inspecting the index by hand."""
    vector = get_embeddings().embed_query(query)
    return _search_tier(tier, [vector], k)
