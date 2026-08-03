"""Report rendering: Markdown for humans, JSON for tooling.

The Markdown layout is deliberately explicit about provenance. Every finding
shows which tier it rests on, so a reader can tell "this violates our documented
standard" from "this is generally good advice with nothing in our docs behind
it" — the distinction the whole pipeline exists to preserve.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import OUTPUTS_DIR
from .schemas import (
    NO_ISSUES_MESSAGE,
    RULE_SOURCE_LABELS,
    ReviewFinding,
    ReviewReport,
)

SEVERITY_ICONS = {
    "Critical": "🛑",
    "High": "🔴",
    "Medium": "🟠",
    "Low": "🟡",
    "Info": "🔵",
}


def _location(finding: ReviewFinding) -> str:
    return f"{finding.file}:{finding.line}" if finding.line else finding.file


def _fence(content: str) -> str:
    """A code fence longer than any backtick run inside ``content``.

    Quoted evidence routinely contains fenced code of its own; a fixed
    three-backtick fence would be closed by it and wreck the rest of the report.
    """
    longest = max((len(run) for run in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


def _render_finding(index: int, finding: ReviewFinding) -> list[str]:
    icon = SEVERITY_ICONS.get(finding.severity, "")
    # Trailing double spaces are Markdown hard line breaks — without them the
    # metadata lines collapse into a single paragraph when rendered.
    lines = [
        f"### {index}. {icon} {finding.severity} — {finding.category}",
        "",
        f"**Location:** `{_location(finding)}`  ",
        f"**Basis:** {RULE_SOURCE_LABELS[finding.rule_source]}  ",
    ]
    if finding.rule_citation:
        lines.append(f"**Source:** {finding.rule_citation}")
    evidence = finding.evidence.strip()
    fence = _fence(evidence)
    lines += [
        "",
        f"**Description**  \n{finding.description}",
        "",
        "**Evidence**",
        "",
        fence,
        evidence,
        fence,
        "",
        f"**Recommendation**  \n{finding.recommendation}",
        "",
    ]
    return lines


def render_markdown(report: ReviewReport) -> str:
    """Render a full review report as Markdown."""
    lines: list[str] = [
        f"# Code Review — {report.repository} PR #{report.pr_number}",
        "",
        f"**{report.title or '(no title)'}**",
        "",
        f"- Author: `{report.author}`",
        f"- Branch: `{report.head_ref}` → `{report.base_ref}`",
    ]
    if report.head_sha:
        lines.append(f"- Head commit: `{report.head_sha[:12]}`")
    lines += [
        f"- Link: {report.pr_url}",
        "",
        "## Summary",
        "",
    ]

    if report.has_findings:
        counts = ", ".join(f"{n} {level}" for level, n in report.severity_counts().items())
        lines += [
            f"{len(report.findings)} finding(s) across {len(report.reviewed_files)} "
            f"reviewed file(s): {counts}.",
        ]
    else:
        lines += [NO_ISSUES_MESSAGE]

    if report.reviewed_files:
        lines += ["", "**Files reviewed**", ""]
        lines += [f"- `{path}`" for path in report.reviewed_files]

    if report.has_findings:
        lines += ["", "## Findings", ""]
        for index, finding in enumerate(report.findings, start=1):
            lines += _render_finding(index, finding)

    if report.positive_notes:
        lines += ["## What the change does well", ""]
        for note in report.positive_notes:
            citation = f" _(matches {note.rule_citation})_" if note.rule_citation else ""
            lines.append(f"- `{note.file}` — {note.note}{citation}")
        lines.append("")

    if report.unverified_rules:
        lines += [
            "## Rules that could not be verified from the provided context",
            "",
            "These concerns are relevant to the change, but the retrieved project "
            "documentation does not cover them. They are reported as gaps rather than "
            "asserted as project rules.",
            "",
        ]
        for rule in report.unverified_rules:
            scope = f"`{rule.file}` — " if rule.file else ""
            lines.append(f"- {scope}**{rule.concern}**  \n  {rule.reason}")
        lines.append("")

    if report.skipped_files:
        lines += ["## Files skipped or only partially reviewed", ""]
        for skipped in report.skipped_files:
            lines.append(f"- `{skipped.path}` — {skipped.reason}")
        lines.append("")

    lines += ["## Context consulted", ""]
    if report.context_sources:
        cited = sorted({f.rule_citation for f in report.findings if f.rule_citation})
        lines += [
            f"{len(report.context_sources)} source(s) were retrieved and available to "
            f"cite while reviewing these files"
            + (f"; {len(cited)} were actually cited in a finding." if cited
               else ". No finding cited a project rule — every finding above rests on "
                    "general best practice.")
            ,
            "",
        ]
        lines += [f"- {source}" for source in report.context_sources]
    else:
        lines += [
            "No project documentation, source code or review history was retrieved. "
            "No finding in this report can be attributed to a project-specific rule — "
            "run `ingest-docs` and `ingest-code` to give the reviewer something to cite.",
        ]
    lines.append("")

    return "\n".join(lines)


def render_json(report: ReviewReport) -> str:
    """Render the report as indented JSON for downstream tooling."""
    return json.dumps(report.model_dump(), indent=2, ensure_ascii=False)


def write_report(
    report: ReviewReport,
    out_dir: str | Path | None = None,
    formats: tuple[str, ...] = ("md", "json"),
) -> list[Path]:
    """Write the report in the requested formats, returning the paths written."""
    directory = Path(out_dir) if out_dir else OUTPUTS_DIR
    directory.mkdir(parents=True, exist_ok=True)

    stem = f"pr-{report.pr_number}-review" if report.pr_number else "review"
    written: list[Path] = []

    if "md" in formats:
        path = directory / f"{stem}.md"
        path.write_text(render_markdown(report), encoding="utf-8")
        written.append(path)
    if "json" in formats:
        path = directory / f"{stem}.json"
        path.write_text(render_json(report), encoding="utf-8")
        written.append(path)

    return written
