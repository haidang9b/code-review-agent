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


def _confidence_note(confidence: float) -> str:
    """Say what a review-level confidence figure actually means for the reader."""
    if confidence >= 0.85:
        return "well-evidenced"
    if confidence >= 0.6:
        return "reasonably evidenced"
    if confidence >= 0.35:
        return "thin evidence — verify before acting"
    return "little evidence — treat as a first pass only"


def _confidence_label(confidence: float) -> str:
    """Render confidence as a percentage plus a word, so it reads at a glance."""
    if confidence >= 0.9:
        word = "certain"
    elif confidence >= 0.7:
        word = "confident"
    elif confidence >= 0.5:
        word = "plausible"
    else:
        word = "speculative"
    return f"{confidence:.0%} ({word})"


def _render_finding(index: int, finding: ReviewFinding) -> list[str]:
    icon = SEVERITY_ICONS.get(finding.severity, "")
    # Trailing double spaces are Markdown hard line breaks — without them the
    # metadata lines collapse into a single paragraph when rendered.
    lines = [
        f"### {index}. {icon} {finding.severity} — {finding.category}",
        "",
        f"**Location:** `{_location(finding)}`  ",
        f"**Confidence:** {_confidence_label(finding.confidence)}  ",
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

    if report.score:
        score = report.score
        lines += [
            "| Code health | Review confidence |",
            "|---|---|",
            f"| **{score.score}/100** — {score.verdict} | "
            f"**{score.confidence:.0%}** — {_confidence_note(score.confidence)} |",
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

    lines += _render_run_metadata(report)

    return "\n".join(lines)


def _render_run_metadata(report: ReviewReport) -> list[str]:
    """Model, cost and score breakdown — what produced this report."""
    usage = report.usage
    lines = ["## Review run", ""]

    model_line = report.review_deployment or "(unknown deployment)"
    if report.model_versions:
        model_line += f" → {', '.join(report.model_versions)}"
    lines += [
        f"- **Model:** `{model_line}`",
        f"- **Model calls:** {usage.model_calls} "
        f"({usage.review_calls} review, {usage.verification_calls} verification)"
        + (f", {usage.embedding_requests} embedding request(s)" if usage.embedding_requests else ""),
    ]

    if usage.total_tokens:
        token_line = (
            f"- **Tokens:** {usage.total_tokens:,} total "
            f"({usage.input_tokens:,} in / {usage.output_tokens:,} out"
        )
        if usage.reasoning_tokens:
            token_line += f", {usage.reasoning_tokens:,} reasoning"
        if usage.cached_input_tokens:
            token_line += f", {usage.cached_input_tokens:,} cached"
        lines.append(token_line + ")")
    else:
        lines.append("- **Tokens:** not reported by the provider")

    if report.duration_seconds:
        lines.append(f"- **Duration:** {report.duration_seconds:g}s")

    if report.score:
        score = report.score
        lines += [
            "",
            f"**Code health {score.score}/100** — 100 minus a severity-weighted penalty "
            f"of {score.penalty:g}, where each finding costs its severity weight "
            f"multiplied by its own confidence.",
            "",
            f"**Review confidence {score.confidence:.0%}** — the mean of the evidence "
            f"signals below. It says how much this review can be trusted, not how good "
            f"the code is.",
            "",
            "| Signal | Value | Meaning |",
            "|---|---|---|",
        ]
        explanations = {
            "file_coverage": "share of the changed files reviewed with full context",
            "documentation_available": "whether any project document was retrievable at all",
            "evidence_grounding": "share of findings backed by a verified project rule",
            "finding_confidence": "mean confidence of the findings themselves",
        }
        for name, value in score.confidence_components.items():
            label = name.replace("_", " ")
            lines.append(f"| {label} | {value:.0%} | {explanations.get(name, '')} |")

        if score.findings_total:
            lines += [
                "",
                f"{score.findings_grounded} of {score.findings_total} finding(s) cite a "
                f"verified project rule; mean finding confidence "
                f"{score.mean_finding_confidence:.0%}.",
            ]
    lines.append("")
    return lines


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
