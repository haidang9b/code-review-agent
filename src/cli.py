"""Command-line entry point.

    python -m src.cli ingest-docs    [--path knowledge] [--force]
    python -m src.cli ingest-code    [--repo <path>] [--force]
    python -m src.cli ingest-reviews --repo owner/name [--limit 200] [--force]
    python -m src.cli review --pr <url> [--out outputs] [--format md,json] [--fail-on High]
    python -m src.cli status
    python -m src.cli search "error handling" [--tier 1] [-k 4]
    python -m src.cli export-graph

Uses stdlib ``argparse`` — the project deliberately carries no CLI framework
dependency.
"""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError
from .github_client import GitHubError
from .schemas import SEVERITY_ORDER

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FINDINGS = 2


def _print(text: str = "") -> None:
    print(text, flush=True)


# ---- commands -------------------------------------------------------------


def cmd_ingest_docs(args: argparse.Namespace) -> int:
    from .ingest import ingest_knowledge

    _print("Ingesting knowledge documents (.md / .pdf / .docx)…")
    summary = ingest_knowledge(path=args.path, force=args.force)
    _print(summary.describe())
    if summary.chunks == 0:
        _print(
            "\nNothing was indexed. Add coding standards, ADRs or design documents "
            "under the knowledge folder (one subfolder per document type)."
        )
    return EXIT_OK


def cmd_ingest_code(args: argparse.Namespace) -> int:
    from .ingest import ingest_code

    _print("Ingesting project source code…")
    summary = ingest_code(repo=args.repo, force=args.force)
    _print(summary.describe())
    return EXIT_OK


def cmd_ingest_reviews(args: argparse.Namespace) -> int:
    from .ingest import ingest_review_history

    _print(f"Ingesting review history from {args.repo}…")
    summary = ingest_review_history(repo=args.repo, limit=args.limit, force=args.force)
    _print(summary.describe())
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    from .config import CHROMA_DIR
    from .vector_store import collection_counts

    _print(f"Vector store: {CHROMA_DIR}")
    counts = collection_counts()
    labels = {
        "knowledge": "tier 1–2  coding standards / architecture",
        "project_code": "tier 3    existing project patterns",
        "review_history": "tier 4    previous review decisions",
    }
    for name, count in counts.items():
        _print(f"  {name:<15} {count:>7} chunks   ({labels.get(name, '')})")

    if not counts.get("knowledge"):
        _print("\nNo knowledge documents indexed — run `ingest-docs` first, or the "
               "reviewer will have no project rule to cite.")
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    from .retriever import citation_for, search

    hits = search(args.query, tier=args.tier, k=args.k)
    if not hits:
        _print("No matches. Has this tier been ingested? Run `status` to check.")
        return EXIT_OK

    for index, hit in enumerate(hits, start=1):
        _print(f"--- {index}. {citation_for(hit)}")
        _print(hit.page_content.strip()[:800])
        _print()
    return EXIT_OK


def cmd_review(args: argparse.Namespace) -> int:
    from .graph import review_pull_request
    from .report import write_report
    from .vector_store import collection_counts

    if not any(collection_counts().values()):
        _print(
            "Warning: no corpus has been ingested. The review will run, but no finding "
            "can be attributed to a project rule. Run `ingest-docs` first.\n"
        )

    _print(f"Reviewing {args.pr}…")
    report = review_pull_request(args.pr)

    formats = tuple(f.strip() for f in args.format.split(",") if f.strip())
    written = write_report(report, out_dir=args.out, formats=formats)

    _print()
    _print(_summarize(report))
    for path in written:
        _print(f"Report written to {path}")

    if args.fail_on:
        threshold = SEVERITY_ORDER[args.fail_on]
        triggering = [f for f in report.findings if SEVERITY_ORDER[f.severity] <= threshold]
        if triggering:
            _print(
                f"\n{len(triggering)} finding(s) at or above {args.fail_on} — exiting {EXIT_FINDINGS}."
            )
            return EXIT_FINDINGS

    return EXIT_OK


def _summarize(report) -> str:
    """One-screen summary of a finished review."""
    lines = [
        f"{report.repository} PR #{report.pr_number} — {len(report.reviewed_files)} file(s) reviewed",
    ]
    if report.findings:
        counts = ", ".join(f"{n} {level}" for level, n in report.severity_counts().items())
        lines.append(f"  findings: {len(report.findings)} ({counts})")
        for finding in report.findings[:10]:
            location = f"{finding.file}:{finding.line}" if finding.line else finding.file
            lines.append(f"    [{finding.severity}/{finding.category}] {location} — {finding.description[:110]}")
        if len(report.findings) > 10:
            lines.append(f"    … and {len(report.findings) - 10} more (see the report)")
    else:
        from .schemas import NO_ISSUES_MESSAGE

        lines.append(f"  {NO_ISSUES_MESSAGE}")

    if report.unverified_rules:
        lines.append(f"  unverified concerns: {len(report.unverified_rules)}")
    if report.skipped_files:
        lines.append(f"  skipped / partial: {len(report.skipped_files)}")
    lines.append(f"  context sources cited: {len(report.context_sources)}")
    return "\n".join(lines)


def cmd_export_graph(args: argparse.Namespace) -> int:
    from .graph import export_graph_png

    try:
        path = export_graph_png(args.out)
    except Exception as exc:  # noqa: BLE001 - PNG rendering needs a network/graphviz backend
        _print(f"Graph PNG export failed ({type(exc).__name__}: {exc}).")
        _print("Falling back to a text rendering:\n")
        from .graph import build_graph

        _print(build_graph().get_graph().draw_ascii())
        return EXIT_OK

    _print(f"Graph written to {path}")
    return EXIT_OK


# ---- parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="AI Code Review Assistant — RAG over your standards, code and review history.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    docs = subparsers.add_parser("ingest-docs", help="index knowledge documents (.md/.pdf/.docx)")
    docs.add_argument("--path", help="knowledge folder (default: KNOWLEDGE_PATH or ./knowledge)")
    docs.add_argument("--force", action="store_true", help="rebuild the collection from scratch")
    docs.set_defaults(handler=cmd_ingest_docs)

    code = subparsers.add_parser("ingest-code", help="index a local checkout's source code")
    code.add_argument("--repo", help="path to the local checkout (default: REPO_PATH)")
    code.add_argument("--force", action="store_true", help="rebuild the collection from scratch")
    code.set_defaults(handler=cmd_ingest_code)

    reviews = subparsers.add_parser("ingest-reviews", help="index past PR review comments")
    reviews.add_argument("--repo", required=True, help="owner/name of the GitHub repository")
    reviews.add_argument("--limit", type=int, default=200, help="most recent comments to fetch")
    reviews.add_argument("--force", action="store_true", help="rebuild the collection from scratch")
    reviews.set_defaults(handler=cmd_ingest_reviews)

    review = subparsers.add_parser("review", help="review a GitHub pull request")
    review.add_argument("--pr", required=True, help="PR URL, or owner/repo#123")
    review.add_argument("--out", help="output directory (default: ./outputs)")
    review.add_argument("--format", default="md,json", help="comma-separated: md, json")
    review.add_argument(
        "--fail-on",
        choices=list(SEVERITY_ORDER),
        help=f"exit {EXIT_FINDINGS} if any finding is at or above this severity",
    )
    review.set_defaults(handler=cmd_review)

    status = subparsers.add_parser("status", help="show indexed chunk counts per collection")
    status.set_defaults(handler=cmd_status)

    search = subparsers.add_parser("search", help="inspect what a tier would retrieve")
    search.add_argument("query")
    search.add_argument("--tier", type=int, default=1, choices=[1, 2, 3, 4])
    search.add_argument("-k", type=int, default=4, help="number of chunks to show")
    search.set_defaults(handler=cmd_search)

    graph = subparsers.add_parser("export-graph", help="write the workflow diagram to a PNG")
    graph.add_argument("--out", help="output path (default: ./outputs/graph.png)")
    graph.set_defaults(handler=cmd_export_graph)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ConfigError, GitHubError, FileNotFoundError) as exc:
        # Expected, actionable failures: show the message, not a traceback.
        _print(f"\nError: {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        _print("\nInterrupted.")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
