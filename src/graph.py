"""The code review pipeline as an explicit LangGraph workflow.

    fetch_pull_request
         │ (conditional edge — nothing reviewable ends the run early)
    plan_review
         │
    retrieve_context
         │
    review_files
         │
    verify_findings          ← the gate that enforces "never invent a project rule"
         │
    assemble_report

Run directly for a demo:  ``python -m src.graph <pr-url>``
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TypedDict

from langchain_core.callbacks import get_usage_metadata_callback
from langgraph.graph import END, START, StateGraph

from .code_loader import IGNORED_DIRS, IGNORED_NAME_PARTS, language_for_path
from .config import OUTPUTS_DIR
from .diff import annotate_patch, changed_symbols, excerpt_around_changes, truncate
from .github_client import ChangedFile, GitHubClient, PullRequest, parse_pr_reference
from .llm import get_review_model, get_settings
from .prompts import build_review_messages, build_verification_messages
from .report import render_markdown
from .retriever import retrieve_for_file
from .schemas import (
    SEVERITY_ORDER,
    FileReview,
    GroundingVerdict,
    PositiveNote,
    ReviewFinding,
    ReviewReport,
    SkippedFile,
    TokenUsage,
    UnverifiedRule,
)
from .scoring import adjust_confidence, compute_score

# A quoted rule this long that appears verbatim in the context needs no model
# call to verify; below it, a coincidental match is too likely.
MIN_VERBATIM_QUOTE_CHARS = 40


class CodeReviewState(TypedDict, total=False):
    """Workflow state shared across nodes.

    Values are plain JSON-able dicts rather than Pydantic instances so that
    LangGraph checkpoints and LangSmith traces stay readable.
    """

    pr_url: str
    pr: dict
    files: list[dict]
    skipped_files: list[dict]
    contexts: dict
    findings: list[dict]
    positive_notes: list[dict]
    unverified_rules: list[dict]
    usage: dict
    started_at: float
    report: dict
    report_markdown: str


# --- Token accounting ------------------------------------------------------


def _merge_usage(base: dict | None, callback_usage: dict, **call_counts: int) -> dict:
    """Fold one node's usage callback output into the running total.

    The callback reports per-model totals; call counts are tracked separately
    because a node's invocation count is not derivable from token sums.
    """
    usage = TokenUsage(**base) if base else TokenUsage()

    for model, metadata in callback_usage.items():
        usage.input_tokens += metadata.get("input_tokens", 0)
        usage.output_tokens += metadata.get("output_tokens", 0)
        usage.total_tokens += metadata.get("total_tokens", 0)
        usage.reasoning_tokens += (metadata.get("output_token_details") or {}).get(
            "reasoning", 0
        )
        usage.cached_input_tokens += (metadata.get("input_token_details") or {}).get(
            "cache_read", 0
        )

        existing = usage.by_model.setdefault(
            model, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        )
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            existing[field] += metadata.get(field, 0)

    for name, count in call_counts.items():
        setattr(usage, name, getattr(usage, name) + count)

    return usage.model_dump()


# --- Helpers ---------------------------------------------------------------


def _is_noise(path: str) -> bool:
    """True for generated, vendored or dependency files not worth reviewing."""
    lowered = path.lower()
    if any(part in lowered for part in IGNORED_NAME_PARTS):
        return True
    return any(segment in IGNORED_DIRS for segment in Path(lowered).parts[:-1])


def _skip(path: str, reason: str) -> dict:
    return SkippedFile(path=path, reason=reason).model_dump()


def _triage(files: list[ChangedFile], max_files: int) -> tuple[list[ChangedFile], list[dict]]:
    """Split changed files into reviewable ones and recorded exclusions."""
    reviewable: list[ChangedFile] = []
    excluded: list[dict] = []

    for changed in files:
        if changed.status == "removed":
            excluded.append(_skip(changed.path, "file deleted in this change"))
        elif not changed.patch:
            excluded.append(
                _skip(changed.path, "no textual diff available (binary, or too large for the GitHub API)")
            )
        elif _is_noise(changed.path):
            excluded.append(_skip(changed.path, "generated, vendored or dependency file"))
        elif len(reviewable) >= max_files:
            excluded.append(
                _skip(changed.path, f"beyond the {max_files}-file limit (MAX_FILES_PER_REVIEW)")
            )
        else:
            reviewable.append(changed)

    return reviewable, excluded


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _quote_is_verbatim(evidence: str, corpus: str) -> bool:
    """Deterministic pre-check: is the quoted rule literally in the context?

    Saves a verification call on the common case where the model quoted the
    standard accurately, and is more reliable than asking a model to do string
    matching.
    """
    quote = _normalize(evidence)
    if len(quote) < MIN_VERBATIM_QUOTE_CHARS:
        return False
    return quote in _normalize(corpus)


def _finding_key(finding: ReviewFinding) -> tuple:
    return (
        finding.file,
        finding.line,
        finding.category,
        _normalize(finding.description)[:80],
    )


# --- Nodes -----------------------------------------------------------------


def fetch_pull_request(state: CodeReviewState) -> dict:
    """Fetch PR metadata, changed files and full file contents from GitHub."""
    s = get_settings()
    ref = parse_pr_reference(state["pr_url"])
    client = GitHubClient()

    pull_request: PullRequest = client.get_pull_request(ref)
    reviewable, excluded = _triage(pull_request.files, s.max_files_per_review)

    files: list[dict] = []
    for changed in reviewable:
        patch, patch_truncated = truncate(changed.patch or "", s.max_patch_chars, "diff")
        if patch_truncated:
            excluded.append(
                _skip(
                    changed.path,
                    f"reviewed with the diff truncated at {s.max_patch_chars} characters",
                )
            )

        content = client.get_file_content(ref, changed.path, pull_request.head_sha)
        content_trimmed = False
        if content:
            # Keep the regions around the changed hunks rather than the head of
            # the file, so a large file still contributes usable context.
            content, content_trimmed = excerpt_around_changes(
                content, patch, s.max_file_chars
            )
        if content_trimmed:
            excluded.append(
                _skip(
                    changed.path,
                    "file too large for full context — reviewed with excerpts around "
                    "the changed hunks only",
                )
            )

        files.append(
            {
                "path": changed.path,
                "status": changed.status,
                "additions": changed.additions,
                "deletions": changed.deletions,
                "patch": patch,
                "content": content,
            }
        )

    return {
        "started_at": time.time(),
        "pr": {
            "url": ref.url,
            "number": ref.number,
            "repository": ref.slug,
            "title": pull_request.title,
            "description": pull_request.description,
            "author": pull_request.author,
            "base_ref": pull_request.base_ref,
            "head_ref": pull_request.head_ref,
            "head_sha": pull_request.head_sha,
            "state": pull_request.state,
        },
        "files": files,
        "skipped_files": excluded,
    }


def plan_review(state: CodeReviewState) -> dict:
    """Annotate diffs with real line numbers and extract changed symbols.

    Entirely deterministic — no model call. The symbols found here are what let
    retrieval look for the project's existing implementation of the same thing.
    """
    planned = []
    for file in state.get("files", []):
        patch = file.get("patch") or ""
        planned.append(
            {
                **file,
                "language": language_for_path(file["path"]),
                "symbols": changed_symbols(patch),
                "annotated_diff": annotate_patch(patch),
            }
        )
    return {"files": planned}


def retrieve_context(state: CodeReviewState) -> dict:
    """Retrieve tier-grouped context for every reviewable file."""
    contexts: dict[str, dict] = {}
    files = state.get("files", [])

    for file in files:
        retrieved = retrieve_for_file(
            path=file["path"],
            language=file.get("language", "text"),
            symbols=file.get("symbols", []),
        )
        contexts[file["path"]] = {
            "block": retrieved.format(),
            "corpus": retrieved.grounding_corpus(),
            "sources": retrieved.sources(),
            "doc_sources": retrieved.document_sources(),
            "empty": retrieved.is_empty,
        }

    # One batched embedding request per file (see retrieve_for_file). Counted
    # here so the usage report covers embeddings, not just chat completions.
    return {
        "contexts": contexts,
        "usage": _merge_usage(state.get("usage"), {}, embedding_requests=len(files)),
    }


def review_files(state: CodeReviewState) -> dict:
    """Review each file against its retrieved context, one structured call each.

    Per-file scoping keeps the context focused and the cited line numbers
    accurate. A failure on one file is recorded and the rest continue — a
    partial review that says what it missed beats no review at all.
    """
    pr = state.get("pr", {})
    contexts = state.get("contexts", {})
    model = get_review_model().with_structured_output(FileReview)

    findings: list[dict] = []
    positives: list[dict] = []
    unverified: list[dict] = []
    failures: list[dict] = []
    calls = 0

    with get_usage_metadata_callback() as usage_callback:
        for file in state.get("files", []):
            path = file["path"]
            context = contexts.get(path, {})
            messages = build_review_messages(
                repository=pr.get("repository", ""),
                pr_number=pr.get("number", 0),
                pr_title=pr.get("title", ""),
                pr_description=pr.get("description", ""),
                path=path,
                status=file.get("status", "modified"),
                language=file.get("language", "text"),
                additions=file.get("additions", 0),
                deletions=file.get("deletions", 0),
                context_block=context.get("block", ""),
                annotated_diff=file.get("annotated_diff", ""),
                file_content=file.get("content"),
            )

            try:
                review: FileReview = model.invoke(messages)
                calls += 1
            except Exception as exc:  # noqa: BLE001 - reported, never silently dropped
                failures.append(
                    _skip(path, f"review call failed: {type(exc).__name__}: {exc}")
                )
                continue

            for finding in review.findings:
                finding.file = finding.file or path
                findings.append(finding.model_dump())
            for note in review.positive_notes:
                note.file = note.file or path
                positives.append(note.model_dump())
            for rule in review.unverified_rules:
                rule.file = rule.file or path
                unverified.append(rule.model_dump())

        collected = dict(usage_callback.usage_metadata)

    return {
        "findings": findings,
        "positive_notes": positives,
        "unverified_rules": unverified,
        "skipped_files": state.get("skipped_files", []) + failures,
        "usage": _merge_usage(state.get("usage"), collected, review_calls=calls),
    }


def verify_findings(state: CodeReviewState) -> dict:
    """Quality gate: no finding may claim a project rule that is not in context.

    Each finding that cites a standard, an ADR, an existing pattern or a past
    review decision is checked against the exact context its file was reviewed
    with. Unsupported claims are not deleted — deleting them would hide a
    possibly real issue — they are downgraded to ``general_best_practice`` and
    the gap is recorded in ``unverified_rules`` so the reader knows the project
    documentation does not cover it.
    """
    contexts = state.get("contexts", {})
    raw_findings = [ReviewFinding(**f) for f in state.get("findings", [])]
    unverified = [UnverifiedRule(**u) for u in state.get("unverified_rules", [])]

    verifier = get_review_model().with_structured_output(GroundingVerdict)

    kept: list[ReviewFinding] = []
    seen: set[tuple] = set()
    calls = 0

    with get_usage_metadata_callback() as usage_callback:
        for finding in raw_findings:
            key = _finding_key(finding)
            if key in seen:
                continue
            seen.add(key)

            corpus = contexts.get(finding.file, {}).get("corpus", "")

            if not finding.needs_grounding:
                finding.confidence = adjust_confidence(
                    finding.confidence, downgraded=False, had_context=bool(corpus)
                )
                kept.append(finding)
                continue

            claimed = finding.rule_source

            if corpus and _quote_is_verbatim(finding.evidence, corpus):
                kept.append(finding)
                continue

            supported = False
            reason = "no project context was retrieved for this file"
            if corpus:
                try:
                    verdict: GroundingVerdict = verifier.invoke(
                        build_verification_messages(finding, corpus)
                    )
                    calls += 1
                    supported, reason = verdict.supported, verdict.reason
                except Exception as exc:  # noqa: BLE001 - fail closed, never fail loud
                    supported, reason = (
                        False,
                        f"verification call failed: {type(exc).__name__}",
                    )

            if supported:
                kept.append(finding)
                continue

            # The issue may still be real, so it survives — but its stated basis
            # was wrong, and the confidence has to reflect that.
            finding.rule_source = "general_best_practice"
            finding.rule_citation = None
            finding.confidence = adjust_confidence(
                finding.confidence, downgraded=True, had_context=bool(corpus)
            )
            kept.append(finding)
            unverified.append(
                UnverifiedRule(
                    concern=f"{finding.category}: {finding.description[:160]}",
                    reason=(
                        f"Reported as a {claimed.replace('_', ' ')} but the rule could not be "
                        f"verified in the retrieved context ({reason}). Reclassified as a "
                        f"general best practice."
                    ),
                    file=finding.file,
                )
            )

        collected = dict(usage_callback.usage_metadata)

    return {
        "findings": [f.model_dump() for f in kept],
        "unverified_rules": [u.model_dump() for u in unverified],
        "usage": _merge_usage(state.get("usage"), collected, verification_calls=calls),
    }


def assemble_report(state: CodeReviewState) -> dict:
    """Aggregate everything into a :class:`ReviewReport` and render Markdown."""
    pr = state.get("pr", {})
    contexts = state.get("contexts", {})

    sources: list[str] = []
    doc_sources: set[str] = set()
    for context in contexts.values():
        for source in context.get("sources", []):
            if source not in sources:
                sources.append(source)
        doc_sources.update(context.get("doc_sources", []))

    findings = [ReviewFinding(**f) for f in state.get("findings", [])]
    # Highest severity first, then most-confident within a severity — the order a
    # reader should work through them in.
    findings.sort(
        key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0)
    )

    skipped = [SkippedFile(**s) for s in state.get("skipped_files", [])]
    reviewed = [f["path"] for f in state.get("files", [])]
    usage = TokenUsage(**state["usage"]) if state.get("usage") else TokenUsage()
    started_at = state.get("started_at")

    report = ReviewReport(
        pr_url=pr.get("url", state.get("pr_url", "")),
        pr_number=pr.get("number", 0),
        repository=pr.get("repository", ""),
        title=pr.get("title", ""),
        author=pr.get("author", ""),
        base_ref=pr.get("base_ref", ""),
        head_ref=pr.get("head_ref", ""),
        head_sha=pr.get("head_sha", ""),
        review_deployment=get_settings().review_deployment,
        model_versions=sorted(usage.by_model),
        usage=usage,
        score=compute_score(findings, reviewed, skipped, len(doc_sources)),
        duration_seconds=round(time.time() - started_at, 1) if started_at else 0.0,
        reviewed_files=reviewed,
        skipped_files=skipped,
        findings=findings,
        positive_notes=[PositiveNote(**p) for p in state.get("positive_notes", [])],
        unverified_rules=[UnverifiedRule(**u) for u in state.get("unverified_rules", [])],
        context_sources=sorted(sources),
    )

    return {"report": report.model_dump(), "report_markdown": render_markdown(report)}


# --- Routing ---------------------------------------------------------------


def route_after_fetch(state: CodeReviewState) -> str:
    """Skip straight to the report when the PR has nothing reviewable."""
    return "plan_review" if state.get("files") else "assemble_report"


# --- Graph assembly --------------------------------------------------------


def build_graph():
    """Build and compile the review workflow."""
    builder = StateGraph(CodeReviewState)

    builder.add_node("fetch_pull_request", fetch_pull_request)
    builder.add_node("plan_review", plan_review)
    builder.add_node("retrieve_context", retrieve_context)
    builder.add_node("review_files", review_files)
    builder.add_node("verify_findings", verify_findings)
    builder.add_node("assemble_report", assemble_report)

    builder.add_edge(START, "fetch_pull_request")
    builder.add_conditional_edges(
        "fetch_pull_request",
        route_after_fetch,
        {"plan_review": "plan_review", "assemble_report": "assemble_report"},
    )
    builder.add_edge("plan_review", "retrieve_context")
    builder.add_edge("retrieve_context", "review_files")
    builder.add_edge("review_files", "verify_findings")
    builder.add_edge("verify_findings", "assemble_report")
    builder.add_edge("assemble_report", END)

    return builder.compile()


def review_pull_request(pr_url: str) -> ReviewReport:
    """Review a pull request end to end and return the aggregated report."""
    final_state = build_graph().invoke({"pr_url": pr_url})
    return ReviewReport(**final_state["report"])


def export_graph_png(path: str | Path | None = None) -> Path:
    """Export the compiled graph as a PNG to ``outputs/graph.png``."""
    path = Path(path) if path else OUTPUTS_DIR / "graph.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    png_bytes = build_graph().get_graph().draw_mermaid_png()
    path.write_bytes(png_bytes)
    return path


def main() -> None:
    """``python -m src.graph <pr-url>`` — review a PR and print the report."""
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if len(sys.argv) < 2:
        print("Usage: python -m src.graph <pull-request-url>")
        raise SystemExit(2)

    print(review_pull_request(sys.argv[1]).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
