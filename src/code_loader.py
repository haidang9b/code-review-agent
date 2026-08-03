"""Source-code loading from a local checkout (tier 3, "Existing Project Patterns").

Indexing the project's own code is what lets the reviewer honour the rule
"if existing project code follows a different pattern, recommend consistency
with the existing implementation" — without a code corpus that instruction is
unenforceable.

Each produced :class:`Document` carries ``source`` (repo-relative POSIX path),
``language`` (used to pick a syntax-aware splitter) and ``tier``.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document

from .config import TIER_PROJECT_PATTERN, resolve_repo_path

# Extension → ``langchain_text_splitters.Language`` member name. Resolved
# lazily in :mod:`src.splitters` so an extension whose Language member does not
# exist in the installed version simply falls back to the generic splitter.
LANGUAGE_BY_SUFFIX = {
    ".py": "PYTHON",
    ".cs": "CSHARP",
    ".js": "JS",
    ".jsx": "JS",
    ".mjs": "JS",
    ".cjs": "JS",
    ".ts": "TS",
    ".tsx": "TS",
    ".java": "JAVA",
    ".go": "GO",
    ".rb": "RUBY",
    ".rs": "RUST",
    ".php": "PHP",
    ".cpp": "CPP",
    ".cc": "CPP",
    ".hpp": "CPP",
    ".c": "C",
    ".h": "C",
    ".kt": "KOTLIN",
    ".kts": "KOTLIN",
    ".scala": "SCALA",
    ".swift": "SWIFT",
    ".sql": "SQL",
    ".ps1": "POWERSHELL",
    ".sh": None,
    ".yml": None,
    ".yaml": None,
    ".tf": None,
    ".vue": None,
    ".razor": None,
    ".cshtml": None,
}

# Directories never worth embedding: dependencies, build output, VCS internals.
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vs",
    ".vscode",
    "node_modules",
    "bower_components",
    "vendor",
    "packages",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "bin",
    "obj",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    "htmlcov",
    ".next",
    ".nuxt",
    ".terraform",
    ".chroma",
    "migrations",
}

# Generated, minified or vendored files: high token cost, no reviewable pattern.
IGNORED_NAME_PARTS = (
    ".min.",
    ".bundle.",
    ".generated.",
    ".designer.",
    ".g.cs",
    ".pb.go",
    "_pb2.py",
    ".lock",
    "-lock.json",
)

MAX_FILE_BYTES = 200_000


def is_indexable(path: Path) -> bool:
    """True when a file is source code worth embedding as a project pattern."""
    if path.suffix.lower() not in LANGUAGE_BY_SUFFIX:
        return False
    name = path.name.lower()
    if name.startswith("."):
        return False
    if any(part in name for part in IGNORED_NAME_PARTS):
        return False
    try:
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def discover_code_files(repo: str | Path | None = None) -> list[Path]:
    """Walk the checkout and return indexable source files, sorted for stability.

    Pruning happens during the walk (rather than filtering afterwards) so large
    dependency trees like ``node_modules`` are never descended into.
    """
    root = resolve_repo_path(repo)
    found: list[Path] = []

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in IGNORED_DIRS:
                    stack.append(entry)
            elif is_indexable(entry):
                found.append(entry)

    return sorted(found)


def load_code(repo: str | Path | None = None) -> tuple[list[Document], list[str]]:
    """Load the checkout's source files as documents.

    Returns the documents plus a list of readable problems, so an
    undecodable file reports itself instead of aborting the whole ingest.
    """
    root = resolve_repo_path(repo)
    documents: list[Document] = []
    problems: list[str] = []

    for path in discover_code_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path.name}: not UTF-8 text, skipped")
            continue
        except OSError as exc:
            problems.append(f"{path.name}: {exc}")
            continue

        if not text.strip():
            continue

        documents.append(
            Document(
                page_content=text,
                metadata={
                    "source": path.relative_to(root).as_posix(),
                    "language": LANGUAGE_BY_SUFFIX.get(path.suffix.lower()) or "text",
                    "tier": TIER_PROJECT_PATTERN,
                    "doc_type": "project_code",
                    "format": "code",
                },
            )
        )

    return documents, problems


def language_for_path(path: str) -> str:
    """Language name for a path, used when reviewing a changed file."""
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower()) or "text"
