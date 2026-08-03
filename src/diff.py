"""Unified-diff parsing: real line numbers and changed symbols.

Two jobs:

1. **Annotate** a patch with the line numbers of the *new* file. Without this a
   model can only guess at line numbers, and a finding that points at the wrong
   line is worse than no finding at all.
2. **Extract changed symbols** so retrieval can look for the project's existing
   implementation of the same function or class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

# Declaration shapes across the languages the code loader indexes. Ordered
# specific → generic; the generic call-site fallback only runs when nothing else
# matched, so ordinary calls do not drown out actual declarations.
_DECLARATION_PATTERNS = (
    re.compile(r"\b(?:async\s+)?def\s+(\w+)"),
    re.compile(r"\bclass\s+(\w+)"),
    re.compile(r"\b(?:function|interface|enum|struct|record|trait|type)\s+(\w+)"),
    re.compile(r"\b(?:const|let|var)\s+(\w+)\s*="),
    re.compile(
        r"\b(?:public|private|protected|internal|static|virtual|override|async|sealed|partial)"
        r"[\w\s<>,\[\]\?]*?\s(\w+)\s*\("
    ),
)
_CALL_FALLBACK = re.compile(r"\b([A-Za-z_]\w{3,})\s*\(")

# Keywords that look like declarations to the fallback regex but are not.
_NOT_SYMBOLS = {
    "if", "for", "while", "switch", "catch", "return", "print", "throw", "new",
    "using", "await", "async", "yield", "assert", "with", "elif", "else", "case",
    "lock", "foreach", "sizeof", "typeof", "nameof", "raise", "import", "from",
}


@dataclass
class Hunk:
    """One ``@@`` section of a patch."""

    new_start: int
    new_count: int
    heading: str
    lines: list[str]


def parse_hunks(patch: str) -> list[Hunk]:
    """Split a unified patch into its hunks."""
    hunks: list[Hunk] = []
    current: Hunk | None = None

    for line in patch.splitlines():
        header = _HUNK_HEADER.match(line)
        if header:
            current = Hunk(
                new_start=int(header.group(3)),
                new_count=int(header.group(4) or 1),
                heading=header.group(5).strip(),
                lines=[],
            )
            hunks.append(current)
        elif current is not None:
            current.lines.append(line)

    return hunks


def annotate_patch(patch: str) -> str:
    """Render a patch with new-file line numbers in the left margin.

    Added and context lines get their real line number; removed lines get a bare
    ``-`` marker because they no longer exist in the reviewed file.
    """
    if not patch:
        return "(no textual diff available for this file)"

    rendered: list[str] = []
    for hunk in parse_hunks(patch):
        suffix = f"  {hunk.heading}" if hunk.heading else ""
        rendered.append(f"@@ hunk starting at line {hunk.new_start}{suffix}")

        line_number = hunk.new_start
        for line in hunk.lines:
            marker, body = (line[:1], line[1:]) if line else (" ", "")
            if marker == "+":
                rendered.append(f"{line_number:>6} + {body}")
                line_number += 1
            elif marker == "-":
                rendered.append(f"{'':>6} - {body}")
            elif marker == "\\":  # "\ No newline at end of file"
                continue
            else:
                rendered.append(f"{line_number:>6}   {body}")
                line_number += 1
        rendered.append("")

    return "\n".join(rendered).rstrip()


def changed_symbols(patch: str, limit: int = 6) -> list[str]:
    """Names of functions, classes and members touched by the patch.

    Hunk headings are checked first: git puts the enclosing function in the
    ``@@ ... @@`` suffix, which is the most reliable signal available.
    """
    if not patch:
        return []

    hunks = parse_hunks(patch)
    candidates: list[str] = []

    for hunk in hunks:
        if hunk.heading:
            candidates.extend(_extract_names(hunk.heading, include_fallback=True))

    changed_text = "\n".join(
        line[1:] for hunk in hunks for line in hunk.lines if line[:1] in "+-"
    )
    candidates.extend(_extract_names(changed_text, include_fallback=False))
    if not candidates:
        candidates.extend(_extract_names(changed_text, include_fallback=True))

    ordered: list[str] = []
    for name in candidates:
        if name not in ordered:
            ordered.append(name)
        if len(ordered) >= limit:
            break
    return ordered


def _extract_names(text: str, include_fallback: bool) -> list[str]:
    names: list[str] = []
    for pattern in _DECLARATION_PATTERNS:
        names.extend(pattern.findall(text))
    if include_fallback and not names:
        names.extend(_CALL_FALLBACK.findall(text))
    return [n for n in names if n.lower() not in _NOT_SYMBOLS]


def excerpt_around_changes(
    content: str,
    patch: str,
    max_chars: int,
    padding: int = 60,
) -> tuple[str, bool]:
    """Return the regions of ``content`` surrounding the patch's hunks.

    A file larger than the budget cannot be sent whole, and cutting it at the
    head keeps the imports while dropping the code that actually changed. This
    keeps a window around each hunk instead, shrinking the window before it
    resorts to cutting anything off, and reports whether anything was omitted.
    """
    if not content:
        return "", False
    if len(content) <= max_chars:
        return content, False

    lines = content.splitlines()
    total = len(lines)
    hunks = parse_hunks(patch)
    if not hunks:
        return truncate(content, max_chars, "file content")

    for window in (padding, padding // 2, padding // 4, 8):
        rendered = _render_windows(lines, hunks, max(window, 4), total)
        if len(rendered) <= max_chars:
            return rendered, True

    cut, _ = truncate(rendered, max_chars, "file context")
    return cut, True


def _render_windows(lines: list[str], hunks: list[Hunk], padding: int, total: int) -> str:
    """Render merged ``padding``-line windows around each hunk, with line numbers."""
    ranges: list[list[int]] = []
    for hunk in hunks:
        start = max(1, hunk.new_start - padding)
        end = min(total, hunk.new_start + hunk.new_count + padding)
        if ranges and start <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])

    blocks: list[str] = []
    previous_end = 0
    for start, end in ranges:
        if start > previous_end + 1:
            blocks.append(f"… lines {previous_end + 1}–{start - 1} omitted …")
        blocks.append(
            "\n".join(f"{n:>6}   {lines[n - 1]}" for n in range(start, end + 1))
        )
        previous_end = end
    if previous_end < total:
        blocks.append(f"… lines {previous_end + 1}–{total} omitted …")

    return "\n".join(blocks)


def truncate(text: str, limit: int, label: str = "content") -> tuple[str, bool]:
    """Cut ``text`` to ``limit`` characters, reporting whether it was cut.

    The caller is expected to surface the boolean — silently truncating context
    would make the review look more complete than it was.
    """
    if len(text) <= limit:
        return text, False
    return (
        text[:limit] + f"\n… [{label} truncated at {limit} characters]",
        True,
    )
