"""Deterministic scoring: code health and review confidence.

Neither number is asked of the model. A model-generated "quality score out of
100" is exactly the kind of unverifiable claim the rest of this project exists to
prevent — it would move with the weather and could not be audited. Both numbers
here are pure functions of the report, so the same findings always produce the
same score, and every input is visible in the output.

Two separate numbers, deliberately not averaged together:

**score** (0–100) — how healthy the change is. Starts at 100 and subtracts a
penalty per finding, weighted by severity and scaled by that finding's
confidence, so a speculative Low costs far less than a certain Critical.

**confidence** (0–1) — how much the review itself should be trusted. The mean of
whichever evidence signals are available: how much of the diff was actually
reviewed, whether project documentation was retrievable, how many findings rest
on a verified project rule, and the findings' own confidence.
"""

from __future__ import annotations

from .schemas import ReviewFinding, ReviewScore, SkippedFile

# Penalty per finding at full confidence. Critical is worth more than five
# Mediums because "ship this and something breaks" is not a quantity of Mediums.
SEVERITY_PENALTY = {
    "Critical": 30.0,
    "High": 14.0,
    "Medium": 5.0,
    "Low": 1.5,
    "Info": 0.0,
}

# Score bands. Worded as a review outcome rather than a letter grade, because
# "B-" tells a developer nothing about whether they can merge.
VERDICT_BANDS = (
    (100, "Clean — no findings"),
    (90, "Minor issues only"),
    (75, "Some issues to address"),
    (55, "Significant issues"),
    (30, "Serious issues"),
    (0, "Blocking issues"),
)

# A finding whose stated basis failed verification keeps its substance but loses
# credibility: the reviewer was wrong about *why* it is a problem.
DOWNGRADE_CONFIDENCE_FACTOR = 0.6

# Reviewing with no retrieved context at all is guesswork about project intent.
NO_CONTEXT_CONFIDENCE_FACTOR = 0.8


def adjust_confidence(confidence: float, *, downgraded: bool, had_context: bool) -> float:
    """Apply the gate's verdict to a model-reported confidence.

    The model supplies a prior; retrieval and verification supply evidence the
    model could not weigh itself. Both factors are multiplicative so the result
    stays in [0, 1] without clamping.
    """
    adjusted = confidence
    if downgraded:
        adjusted *= DOWNGRADE_CONFIDENCE_FACTOR
    if not had_context:
        adjusted *= NO_CONTEXT_CONFIDENCE_FACTOR
    return round(max(0.0, min(1.0, adjusted)), 3)


def _verdict(score: int) -> str:
    for threshold, wording in VERDICT_BANDS:
        if score >= threshold:
            return wording
    return VERDICT_BANDS[-1][1]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def file_coverage(reviewed: list[str], skipped: list[SkippedFile]) -> float:
    """Fraction of the change that was actually seen, in [0, 1].

    A file reviewed with truncated context counts as half: it was examined, but
    not in full. A file not reviewed at all counts as zero. Files excluded for
    being irrelevant — deleted, binary, generated — are not counted against
    coverage, because not reviewing them is correct rather than a gap.
    """
    reviewed_set = set(reviewed)
    partial = {s.path for s in skipped if s.path in reviewed_set}
    missed = {
        s.path
        for s in skipped
        if s.path not in reviewed_set and "review call failed" in s.reason
    }

    total = len(reviewed_set | missed)
    if total == 0:
        return 0.0

    full = len(reviewed_set - partial)
    return round((full + 0.5 * len(partial)) / total, 3)


def compute_score(
    findings: list[ReviewFinding],
    reviewed_files: list[str],
    skipped_files: list[SkippedFile],
    doc_sources: int,
) -> ReviewScore:
    """Build a :class:`ReviewScore` from a finished review.

    Args:
        findings: the findings that survived verification.
        reviewed_files: paths that were reviewed.
        skipped_files: exclusions and partial-review notes.
        doc_sources: how many retrieved sources were project *documents* (as
            opposed to source code), i.e. whether any rule could have been cited.
    """
    penalty = sum(
        SEVERITY_PENALTY[f.severity] * f.confidence for f in findings
    )
    score = max(0, min(100, round(100 - penalty)))

    grounded = [f for f in findings if f.needs_grounding]
    confidences = [f.confidence for f in findings]

    # Only signals that actually carry information are averaged. With no
    # findings, "how many findings are grounded" says nothing about trust, so it
    # is left out rather than counted as a perfect or a zero score.
    components: dict[str, float] = {
        "file_coverage": file_coverage(reviewed_files, skipped_files),
        "documentation_available": 1.0 if doc_sources else 0.0,
    }
    if findings:
        components["evidence_grounding"] = round(len(grounded) / len(findings), 3)
        components["finding_confidence"] = round(_mean(confidences), 3)

    severity_counts: dict[str, int] = {}
    for finding in findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1

    return ReviewScore(
        score=score,
        verdict=_verdict(score),
        penalty=round(penalty, 2),
        confidence=round(_mean(list(components.values())), 3),
        confidence_components=components,
        findings_total=len(findings),
        findings_grounded=len(grounded),
        mean_finding_confidence=round(_mean(confidences), 3),
        severity_counts={
            level: severity_counts[level]
            for level in SEVERITY_PENALTY
            if level in severity_counts
        },
    )
