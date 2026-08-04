"""Pydantic schemas for structured output.

These models are passed to ``llm.with_structured_output(...)`` so the model is
constrained to emit valid, typed JSON — no manual parsing. The vocabulary here
is exactly the vocabulary of ``prompts/reviewer.md``: categories, severities and
the rule-source tiers that the verification gate checks.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["Critical", "High", "Medium", "Low", "Info"]

Category = Literal[
    "Bug",
    "Security",
    "Performance",
    "Architecture",
    "Clean Code",
    "Naming",
    "Style",
    "Testing",
    "Maintainability",
    "Documentation",
]

# Which priority tier a finding rests on. ``general_best_practice`` is the only
# value that does not require supporting evidence in the retrieved context —
# and the verification gate rewrites any unsupported finding to it.
RuleSource = Literal[
    "coding_standard",
    "architecture",
    "project_pattern",
    "previous_review",
    "general_best_practice",
]

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}

RULE_SOURCE_LABELS = {
    "coding_standard": "Coding Standard",
    "architecture": "Architecture Rule",
    "project_pattern": "Existing Project Pattern",
    "previous_review": "Previous Review Decision",
    "general_best_practice": "General Best Practice (not verified against project docs)",
}

NO_ISSUES_MESSAGE = (
    "The code follows the available coding standards and no significant issues "
    "were identified."
)


class ReviewFinding(BaseModel):
    """A single issue found in the reviewed code."""

    category: Category = Field(description="Issue category.")
    severity: Severity = Field(description="Severity: Critical, High, Medium, Low or Info.")
    file: str = Field(default="", description="Path of the file the issue is in.")
    line: Optional[int] = Field(
        default=None,
        description=(
            "Line number in the NEW version of the file, taken from the line "
            "numbers shown in the diff margin. Null if the issue is file-wide."
        ),
    )
    description: str = Field(
        description="What is wrong AND why it matters. Never state a problem without its consequence."
    )
    evidence: str = Field(
        description=(
            "The basis for the finding: the rule quoted verbatim from the retrieved "
            "context, or the offending code when the finding is a plain defect."
        )
    )
    rule_source: RuleSource = Field(
        description=(
            "Which context tier supports this finding. Use 'general_best_practice' "
            "whenever the retrieved context does not contain the rule."
        )
    )
    rule_citation: Optional[str] = Field(
        default=None,
        description="Where the rule came from, e.g. 'coding-standards.md › Error Handling'.",
    )
    recommendation: str = Field(description="Concrete, actionable change to make.")
    confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "How certain you are that this is a genuine issue, from 0.0 to 1.0. "
            "0.9+ = the code plainly does this and the rule plainly forbids it. "
            "0.7-0.9 = confident with minor ambiguity. 0.5-0.7 = plausible but "
            "depends on context you cannot see. Below 0.5 = speculative. Do not "
            "inflate: an honest low number is more useful than a false high one."
        ),
    )

    @property
    def needs_grounding(self) -> bool:
        """True when this finding claims a project-specific rule."""
        return self.rule_source != "general_best_practice"


class PositiveNote(BaseModel):
    """A good practice worth acknowledging."""

    file: str = Field(default="", description="Path of the file being praised.")
    note: str = Field(description="What the code does well, and why that is good.")
    rule_citation: Optional[str] = Field(
        default=None,
        description="The standard or existing pattern the code adheres to, if any.",
    )


class UnverifiedRule(BaseModel):
    """A concern that is relevant but unsupported by the retrieved context."""

    concern: str = Field(description="The concern that could not be checked.")
    reason: str = Field(
        description="Why it could not be verified from the provided context."
    )
    file: str = Field(default="", description="File the concern relates to.")


class FileReview(BaseModel):
    """The reviewer's structured output for one changed file."""

    findings: list[ReviewFinding] = Field(
        default_factory=list, description="Issues found. Empty if there are none."
    )
    positive_notes: list[PositiveNote] = Field(
        default_factory=list, description="Good practices observed in the code."
    )
    unverified_rules: list[UnverifiedRule] = Field(
        default_factory=list,
        description="Relevant concerns with no supporting rule in the retrieved context.",
    )


class GroundingVerdict(BaseModel):
    """Verdict of the verification gate on one finding's cited evidence."""

    supported: bool = Field(
        description=(
            "True only if the cited rule genuinely appears in the retrieved context. "
            "False if it is paraphrased beyond recognition, absent, or invented."
        )
    )
    reason: str = Field(description="Brief justification for the verdict.")
    matched_quote: Optional[str] = Field(
        default=None,
        description="The passage from the context that supports the finding, if found.",
    )


class SkippedFile(BaseModel):
    """A changed file that was deliberately not reviewed."""

    path: str
    reason: str


class TokenUsage(BaseModel):
    """Token accounting for one review run.

    Collected from ``AIMessage.usage_metadata`` via LangChain's usage callback,
    so the numbers are the provider's own, not an estimate.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # Reasoning-family deployments bill thinking tokens inside output_tokens;
    # broken out here because it is often most of the cost.
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0

    review_calls: int = 0
    verification_calls: int = 0
    embedding_requests: int = 0

    # Resolved model id → its own usage, e.g. {"gpt-5.1-2025-11-13": {...}}.
    by_model: dict[str, dict] = Field(default_factory=dict)

    @property
    def model_calls(self) -> int:
        return self.review_calls + self.verification_calls


class ReviewScore(BaseModel):
    """Two separate numbers, deliberately not merged into one.

    ``score`` answers "how healthy is this change" — it is derived only from the
    findings. ``confidence`` answers "how much should I trust this review" — it is
    derived only from how much evidence was available. Averaging the two would
    produce a number that means neither thing.

    Both are computed deterministically from the report (see :mod:`src.scoring`);
    neither is asked of the model, so the same findings always score the same.
    """

    score: int = Field(ge=0, le=100, description="Code health, 100 = no findings.")
    verdict: str = Field(description="Plain-language reading of the score.")
    penalty: float = Field(description="Total severity-weighted penalty subtracted from 100.")

    confidence: float = Field(
        ge=0.0, le=1.0, description="How much evidence this review rests on."
    )
    confidence_components: dict[str, float] = Field(
        default_factory=dict,
        description="The individual [0,1] signals averaged into `confidence`.",
    )

    findings_total: int = 0
    findings_grounded: int = Field(
        default=0, description="Findings backed by a verified project rule."
    )
    mean_finding_confidence: float = 0.0
    severity_counts: dict[str, int] = Field(default_factory=dict)


class ReviewReport(BaseModel):
    """The complete, aggregated result of reviewing a pull request."""

    pr_url: str = ""
    pr_number: int = 0
    repository: str = ""
    title: str = ""
    author: str = ""
    base_ref: str = ""
    head_ref: str = ""
    head_sha: str = ""

    # ---- run metadata ----
    # Which model produced this, what it cost, and how it scored. Recorded so a
    # report read weeks later is still interpretable: findings from a small
    # deployment do not carry the same weight as findings from a large one.
    review_deployment: str = Field(
        default="", description="Azure deployment name used for review + verification."
    )
    model_versions: list[str] = Field(
        default_factory=list, description="Resolved model ids the provider reported."
    )
    usage: TokenUsage = Field(default_factory=TokenUsage)
    score: Optional[ReviewScore] = None
    duration_seconds: float = 0.0

    reviewed_files: list[str] = Field(default_factory=list)
    skipped_files: list[SkippedFile] = Field(default_factory=list)

    findings: list[ReviewFinding] = Field(default_factory=list)
    positive_notes: list[PositiveNote] = Field(default_factory=list)
    unverified_rules: list[UnverifiedRule] = Field(default_factory=list)
    context_sources: list[str] = Field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def severity_counts(self) -> dict[str, int]:
        """Findings per severity, in severity order, omitting empty levels."""
        counts = {level: 0 for level in SEVERITY_ORDER}
        for finding in self.findings:
            counts[finding.severity] += 1
        return {level: n for level, n in counts.items() if n}
