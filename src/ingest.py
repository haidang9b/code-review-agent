"""Corpus ingestion: knowledge documents, project source, review history.

Each function loads, splits and indexes one corpus, and returns a small summary
the CLI prints. Problems (an unreadable PDF, a non-UTF-8 source file) are
returned rather than raised so one bad file cannot block the rest — but they are
always reported, never swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.documents import Document

from .code_loader import load_code
from .config import (
    CODE_COLLECTION,
    KNOWLEDGE_COLLECTION,
    REVIEW_COLLECTION,
    TIER_PREVIOUS_REVIEW,
    resolve_knowledge_path,
    resolve_repo_path,
)
from .github_client import GitHubClient, parse_repo_reference
from .loaders import load_knowledge
from .splitters import (
    split_code_documents,
    split_knowledge_documents,
    split_review_documents,
)
from .vector_store import index_documents


@dataclass
class IngestSummary:
    """Outcome of one ingest run."""

    collection: str
    source: str
    documents: int = 0
    chunks: int = 0
    indexed: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"Collection '{self.collection}' ← {self.source}",
            f"  documents: {self.documents}   chunks: {self.chunks}   indexed: {self.indexed}",
        ]
        if self.breakdown:
            detail = "   ".join(f"{k}={v}" for k, v in sorted(self.breakdown.items()))
            lines.append(f"  breakdown: {detail}")
        if self.problems:
            lines.append(f"  problems ({len(self.problems)}):")
            lines += [f"    - {problem}" for problem in self.problems]
        return "\n".join(lines)


def _count_by(chunks: list[Document], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        value = str(chunk.metadata.get(key, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def ingest_knowledge(
    path: str | Path | None = None,
    force: bool = False,
    progress: bool = True,
) -> IngestSummary:
    """Index coding standards, ADRs and design documents (tiers 1–2)."""
    root = resolve_knowledge_path(path)
    documents, problems = load_knowledge(root)
    chunks = split_knowledge_documents(documents)
    indexed = index_documents(KNOWLEDGE_COLLECTION, chunks, force=force, progress=progress)

    return IngestSummary(
        collection=KNOWLEDGE_COLLECTION,
        source=str(root),
        documents=len(documents),
        chunks=len(chunks),
        indexed=indexed,
        breakdown=_count_by(chunks, "doc_type"),
        problems=problems,
    )


def ingest_code(
    repo: str | Path | None = None,
    force: bool = False,
    progress: bool = True,
) -> IngestSummary:
    """Index a local checkout's source as "Existing Project Patterns" (tier 3)."""
    root = resolve_repo_path(repo)
    documents, problems = load_code(root)
    chunks = split_code_documents(documents)
    indexed = index_documents(CODE_COLLECTION, chunks, force=force, progress=progress)

    return IngestSummary(
        collection=CODE_COLLECTION,
        source=str(root),
        documents=len(documents),
        chunks=len(chunks),
        indexed=indexed,
        breakdown=_count_by(chunks, "language"),
        problems=problems,
    )


def ingest_review_history(
    repo: str,
    limit: int = 200,
    force: bool = False,
    progress: bool = True,
) -> IngestSummary:
    """Index past PR review comments as "Previous Review Decisions" (tier 4).

    Past decisions are what stop the assistant from re-litigating a question the
    team already settled, so they are indexed with the reviewer's name, the file
    they commented on, and a link back to the original comment.
    """
    owner, name = parse_repo_reference(repo)
    comments = GitHubClient().get_review_comments(owner, name, limit=limit)

    documents = [
        Document(
            page_content=(
                f"Review comment by {comment['author']} on {comment['path'] or 'the pull request'} "
                f"(PR #{comment['pr_number']}):\n{comment['body']}"
            ),
            metadata={
                "source": f"{owner}/{name}#{comment['pr_number']} review by {comment['author']}",
                "doc_type": "previous_review",
                "tier": TIER_PREVIOUS_REVIEW,
                "format": "review",
                "path": comment["path"],
                "url": comment["url"],
                "created_at": comment["created_at"],
            },
        )
        for comment in comments
    ]

    chunks = split_review_documents(documents)
    indexed = index_documents(REVIEW_COLLECTION, chunks, force=force, progress=progress)

    return IngestSummary(
        collection=REVIEW_COLLECTION,
        source=f"{owner}/{name} (last {limit} review comments)",
        documents=len(documents),
        chunks=len(chunks),
        indexed=indexed,
        problems=(
            []
            if documents
            else ["no human review comments found — the repo may have none, or the token lacks access"]
        ),
    )
