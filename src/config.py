"""Central configuration: environment loading, validation, paths and tiers.

All secrets come from the environment (a local ``.env`` file). Nothing is
hard-coded here. Import ``load_settings`` from this module wherever
configuration is needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (one level above this file's ``src`` dir).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ---- Paths ----------------------------------------------------------------
CHROMA_DIR = PROJECT_ROOT / ".chroma"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
DEFAULT_KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"

# ---- Collections ----------------------------------------------------------
KNOWLEDGE_COLLECTION = "knowledge"
CODE_COLLECTION = "project_code"
REVIEW_COLLECTION = "review_history"

# ---- Context priority tiers ----------------------------------------------
# The reviewer prompt mandates this ordering:
#   1. Coding Standards  2. Architecture Rules  3. Existing Project Patterns
#   4. Previous Review Decisions  5. General Best Practices
# Tier 5 is deliberately absent: it is the model's own knowledge and is never
# retrieved. Findings resting on it must be labelled ``general_best_practice``.
TIER_CODING_STANDARD = 1
TIER_ARCHITECTURE = 2
TIER_PROJECT_PATTERN = 3
TIER_PREVIOUS_REVIEW = 4

TIER_LABELS = {
    TIER_CODING_STANDARD: "TIER 1 — CODING STANDARDS (highest authority)",
    TIER_ARCHITECTURE: "TIER 2 — ARCHITECTURE RULES",
    TIER_PROJECT_PATTERN: "TIER 3 — EXISTING PROJECT PATTERNS (source code)",
    TIER_PREVIOUS_REVIEW: "TIER 4 — PREVIOUS REVIEW DECISIONS",
}

# ``knowledge/<folder>/`` determines a document's ``doc_type`` and therefore its
# tier. Folder names are matched case-insensitively; unknown folders fall back to
# ``other`` at tier 1 so a dropped-in document is never silently ignored.
DOC_TYPE_TIERS = {
    "standards": TIER_CODING_STANDARD,
    "conventions": TIER_CODING_STANDARD,
    "testing": TIER_CODING_STANDARD,
    "security": TIER_CODING_STANDARD,
    "performance": TIER_CODING_STANDARD,
    "architecture": TIER_ARCHITECTURE,
    "patterns": TIER_ARCHITECTURE,
    "adr": TIER_ARCHITECTURE,
    "design": TIER_ARCHITECTURE,
    "api": TIER_ARCHITECTURE,
    "other": TIER_CODING_STANDARD,
}


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing or invalid."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Required environment variable '{name}' is not set. "
            f"Copy .env.example to .env and fill in your Azure OpenAI values."
        )
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable '{name}' must be an integer.") from exc


@dataclass(frozen=True)
class Settings:
    """Resolved configuration pulled from the environment."""

    azure_api_key: str
    azure_endpoint: str
    azure_chat_deployment: str
    azure_api_version: str
    azure_embedding_deployment: str

    # Optional stronger deployment used only for review + verification calls.
    azure_review_deployment: str | None = None

    github_token: str | None = None
    github_api_url: str = "https://api.github.com"

    repo_path: Path | None = None
    knowledge_path: Path = DEFAULT_KNOWLEDGE_DIR

    langsmith_api_key: str | None = None
    langsmith_project: str = "ai-code-review"

    # Chunking defaults (overridable by callers).
    chunk_size: int = 1200
    chunk_overlap: int = 150
    code_chunk_size: int = 1600
    code_chunk_overlap: int = 200

    # Retrieval budget per priority tier. Tier 1 gets the largest share because
    # the concern probes (error handling, secrets, N+1, testing, …) all compete
    # for it, and a budget smaller than the probe count starves the later ones.
    k_coding_standard: int = 12
    k_architecture: int = 6
    k_project_pattern: int = 5
    k_previous_review: int = 4

    # Review limits.
    max_files_per_review: int = 25
    max_patch_chars: int = 12000
    max_file_chars: int = 20000

    @property
    def langsmith_enabled(self) -> bool:
        return bool(self.langsmith_api_key)

    @property
    def review_deployment(self) -> str:
        """Deployment used for reviewing; falls back to the default chat model."""
        return self.azure_review_deployment or self.azure_chat_deployment

    def tier_k(self, tier: int) -> int:
        """Number of chunks to retrieve for a given priority tier."""
        return {
            TIER_CODING_STANDARD: self.k_coding_standard,
            TIER_ARCHITECTURE: self.k_architecture,
            TIER_PROJECT_PATTERN: self.k_project_pattern,
            TIER_PREVIOUS_REVIEW: self.k_previous_review,
        }[tier]


def load_settings() -> Settings:
    """Build a validated :class:`Settings` from the current environment.

    Raises :class:`ConfigError` with a legible message if a mandatory Azure
    variable is absent, so misconfiguration fails fast rather than crashing
    deep inside a client call. GitHub and repo settings are optional here and
    validated by the commands that actually need them.
    """

    repo_path_raw = os.getenv("REPO_PATH")
    knowledge_raw = os.getenv("KNOWLEDGE_PATH")

    settings = Settings(
        azure_api_key=_require("AZURE_OPENAI_API_KEY"),
        azure_endpoint=_require("AZURE_OPENAI_ENDPOINT"),
        azure_chat_deployment=_require("AZURE_OPENAI_DEPLOYMENT_NAME"),
        azure_api_version=_require("AZURE_OPENAI_API_VERSION"),
        azure_embedding_deployment=_require("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_review_deployment=os.getenv("AZURE_OPENAI_REVIEW_DEPLOYMENT") or None,
        github_token=os.getenv("GITHUB_TOKEN") or None,
        github_api_url=(
            os.getenv("GITHUB_API_URL") or "https://api.github.com"
        ).rstrip("/"),
        repo_path=Path(repo_path_raw).expanduser() if repo_path_raw else None,
        knowledge_path=(
            Path(knowledge_raw).expanduser() if knowledge_raw else DEFAULT_KNOWLEDGE_DIR
        ),
        langsmith_api_key=os.getenv("LANGSMITH_API_KEY") or None,
        langsmith_project=os.getenv("LANGSMITH_PROJECT") or "ai-code-review",
        max_files_per_review=_int_env("MAX_FILES_PER_REVIEW", 25),
        max_patch_chars=_int_env("MAX_PATCH_CHARS", 12000),
    )

    # Enable LangSmith tracing automatically when a key is present — and turn it
    # off when there is none. Leaving a stale LANGSMITH_TRACING=true with no key
    # makes every model call emit a 401 from the tracing exporter, which buries
    # the review output in noise.
    if settings.langsmith_enabled:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    return settings


def resolve_knowledge_path(override: str | Path | None = None) -> Path:
    """Return the knowledge folder to ingest, preferring an explicit override."""
    path = Path(override).expanduser() if override else load_settings().knowledge_path
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def resolve_repo_path(override: str | Path | None = None) -> Path:
    """Return the local checkout to index, preferring an explicit override.

    Raises :class:`ConfigError` when neither an override nor ``REPO_PATH`` is
    available, because there is no sensible default for someone else's repo.
    """
    raw = override or load_settings().repo_path
    if not raw:
        raise ConfigError(
            "No repository path given. Pass --repo <path> or set REPO_PATH in .env "
            "to a local checkout of the project whose code should be indexed."
        )
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ConfigError(f"Repository path is not a directory: {path}")
    return path
