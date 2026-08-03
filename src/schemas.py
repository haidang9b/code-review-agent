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
