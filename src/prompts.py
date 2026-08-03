"""Prompt assembly.

The reviewer instructions live in ``prompts/reviewer.md`` rather than in this
module so the team can tune the review policy without touching Python.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from .config import PROMPTS_DIR
from .diff import truncate
from .schemas import RULE_SOURCE_LABELS, ReviewFinding

REVIEWER_PROMPT_PATH = PROMPTS_DIR / "reviewer.md"

MAX_PR_DESCRIPTION_CHARS = 2000


@lru_cache(maxsize=1)
def reviewer_system_prompt() -> str:
    """Load the reviewer instructions from ``prompts/reviewer.md``."""
    if not REVIEWER_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Reviewer prompt not found at {REVIEWER_PROMPT_PATH}. It defines the "
            f"review policy and is required."
        )
    return REVIEWER_PROMPT_PATH.read_text(encoding="utf-8")


def build_review_messages(
    *,
    repository: str,
    pr_number: int,
    pr_title: str,
    pr_description: str,
    path: str,
    status: str,
    language: str,
    additions: int,
    deletions: int,
    context_block: str,
    annotated_diff: str,
    file_content: str | None,
) -> list[BaseMessage]:
    """Assemble the review request for one changed file.

    Retrieved context comes *before* the code deliberately: the rules should
    frame how the diff is read, not be consulted afterwards to justify an
    opinion already formed.
    """
    description, _ = truncate(
        pr_description.strip() or "(no description provided)",
        MAX_PR_DESCRIPTION_CHARS,
        "pull request description",
    )

    sections = [
        "## Pull request",
        f"Repository: {repository}",
        f"Pull request: #{pr_number} — {pr_title or '(no title)'}",
        f"Description:\n{description}",
        "",
        "## File under review",
        f"Path: {path}",
        f"Change type: {status} (+{additions} / -{deletions})",
        f"Language: {language}",
        "",
        "## Retrieved context",
        "Everything you may treat as a project rule is below, grouped by priority "
        "tier. Anything not present here is NOT a project rule.",
        "",
        context_block,
        "",
    ]

    if file_content:
        sections += [
            "## Surrounding file context at the head commit",
            "The file as it stands after the change (large files are shown as excerpts "
            "around the changed regions, with omissions marked). Review the changed "
            "lines, but use this to judge whether an issue is already handled "
            "elsewhere in the file.",
            "",
            "```",
            file_content,
            "```",
            "",
        ]

    sections += [
        "## Change to review",
        "Line numbers in the left margin are the line numbers of the NEW version of "
        "the file. Use them for the `line` field. Lines marked `-` were removed.",
        "",
        "```diff",
        annotated_diff,
        "```",
        "",
        "## Your task",
        "Review this change against the retrieved context and report findings, "
        "positive notes, and any relevant concern you could not verify. Quote rules "
        "verbatim in `evidence`. If the context contains no rule for an issue you are "
        "certain about, report it with `rule_source: general_best_practice`. Do not "
        "invent a project rule, and do not report style preferences that no retrieved "
        "document supports.",
    ]

    return [
        SystemMessage(content=reviewer_system_prompt()),
        HumanMessage(content="\n".join(sections)),
    ]


VERIFIER_SYSTEM_PROMPT = (
    "You are a strict evidence checker in a code review pipeline. You do not judge "
    "whether the finding is a good idea — only whether the rule it claims is actually "
    "present in the retrieved project context.\n\n"
    "Set supported = true ONLY if the context contains a rule that says what the "
    "finding claims it says. The wording may differ, but the obligation must be the "
    "same, and it must apply to the situation described.\n\n"
    "Set supported = false when:\n"
    "- the context does not mention the rule at all;\n"
    "- the context is about something related but does not impose this requirement;\n"
    "- the quoted evidence does not appear in the context;\n"
    "- the finding generalises a specific rule into a broader one.\n\n"
    "When in doubt, answer false. A finding wrongly presented as a project rule is a "
    "worse outcome than one correctly labelled a general best practice."
)


def build_verification_messages(finding: ReviewFinding, context_corpus: str) -> list[BaseMessage]:
    """Assemble the groundedness check for one finding."""
    claim = "\n".join(
        [
            f"Claimed rule source: {RULE_SOURCE_LABELS[finding.rule_source]}",
            f"Claimed citation: {finding.rule_citation or '(none given)'}",
            f"Category / severity: {finding.category} / {finding.severity}",
            f"Description: {finding.description}",
            f"Quoted evidence: {finding.evidence}",
            f"Recommendation: {finding.recommendation}",
        ]
    )

    return [
        SystemMessage(content=VERIFIER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"RETRIEVED PROJECT CONTEXT:\n{context_corpus}\n\n"
                f"FINDING TO CHECK:\n{claim}\n\n"
                "Is the claimed project rule genuinely present in the context above?"
            )
        ),
    ]
