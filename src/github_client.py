"""Minimal GitHub REST client for pull requests and review history.

Uses ``requests`` directly rather than a GitHub SDK: three endpoints are needed
and pinning a whole SDK for them is not worth the dependency.

Only read endpoints are implemented. Nothing here writes to GitHub — reports
stay local by design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

from .llm import get_settings

TIMEOUT_SECONDS = 30
PER_PAGE = 100
# GitHub caps pull-request file listings at 3000 files / 30 pages.
MAX_PAGES = 30

_PR_URL_PATTERN = re.compile(
    r"""(?:https?://[^/]+/)?      # optional scheme + host
        (?P<owner>[\w.-]+)/
        (?P<repo>[\w.-]+)
        (?:/(?:pull|pulls)/|\#)   # /pull/123 or owner/repo#123
        (?P<number>\d+)""",
    re.VERBOSE,
)


class GitHubError(RuntimeError):
    """Raised when the GitHub API cannot be used or returns an error."""


@dataclass(frozen=True)
class PullRequestRef:
    """Identifies a pull request."""

    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.slug}/pull/{self.number}"


@dataclass
class ChangedFile:
    """One file in a pull request diff."""

    path: str
    status: str
    additions: int
    deletions: int
    patch: str | None = None
    previous_path: str | None = None
    content: str | None = None


@dataclass
class PullRequest:
    """The subset of PR metadata the reviewer needs."""

    ref: PullRequestRef
    title: str
    description: str
    author: str
    base_ref: str
    head_ref: str
    head_sha: str
    state: str
    files: list[ChangedFile] = field(default_factory=list)


def parse_pr_reference(value: str) -> PullRequestRef:
    """Parse a PR URL, ``owner/repo#123`` or ``owner/repo/pull/123``."""
    match = _PR_URL_PATTERN.search(value.strip())
    if not match:
        raise GitHubError(
            f"Could not parse a pull request from '{value}'. Expected something "
            f"like https://github.com/owner/repo/pull/123 or owner/repo#123."
        )
    return PullRequestRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=int(match.group("number")),
    )


def parse_repo_reference(value: str) -> tuple[str, str]:
    """Parse ``owner/repo`` (or a repo URL) into its two parts."""
    cleaned = value.strip().rstrip("/")
    cleaned = re.sub(r"^https?://[^/]+/", "", cleaned)
    cleaned = re.sub(r"\.git$", "", cleaned)
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) < 2:
        raise GitHubError(f"Expected 'owner/repo', got '{value}'.")
    return parts[0], parts[1]


class GitHubClient:
    """Read-only GitHub API client.

    Works unauthenticated against public repositories (60 requests/hour); a
    ``GITHUB_TOKEN`` raises that to 5000/hour and is required for private repos.
    """

    def __init__(self, token: str | None = None, api_url: str | None = None) -> None:
        s = get_settings()
        self.api_url = (api_url or s.github_api_url).rstrip("/")
        self.token = token if token is not None else s.github_token

        self._session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-code-review",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._session.headers.update(headers)

    # ---- transport --------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        url = f"{self.api_url}{path}"
        try:
            response = self._session.get(url, params=params, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise GitHubError(f"GitHub request to {url} failed: {exc}") from exc

        if response.status_code == 404:
            raise GitHubError(
                f"Not found: {url}. Check the owner/repo/number, and set "
                f"GITHUB_TOKEN if the repository is private."
            )
        if response.status_code in (401, 403):
            remaining = response.headers.get("X-RateLimit-Remaining")
            hint = (
                "rate limit exhausted — set GITHUB_TOKEN in .env to raise it"
                if remaining == "0"
                else "authentication or permission problem — check GITHUB_TOKEN"
            )
            raise GitHubError(f"GitHub returned {response.status_code} for {url} ({hint}).")
        if not response.ok:
            raise GitHubError(
                f"GitHub returned {response.status_code} for {url}: {response.text[:300]}"
            )
        return response

    def _paginate(self, path: str, params: dict | None = None, max_items: int | None = None) -> list[dict]:
        """Collect pages until exhausted, ``MAX_PAGES``, or ``max_items``."""
        collected: list[dict] = []
        page_params = dict(params or {})
        page_params["per_page"] = PER_PAGE

        for page in range(1, MAX_PAGES + 1):
            page_params["page"] = page
            payload = self._get(path, page_params).json()
            if not isinstance(payload, list):
                raise GitHubError(f"Expected a list from {path}, got {type(payload).__name__}.")
            collected.extend(payload)
            if len(payload) < PER_PAGE:
                break
            if max_items is not None and len(collected) >= max_items:
                break

        return collected[:max_items] if max_items is not None else collected

    # ---- pull requests ----------------------------------------------------

    def get_pull_request(self, ref: PullRequestRef) -> PullRequest:
        """Fetch PR metadata and its changed files (with patches)."""
        payload = self._get(f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}").json()

        pull_request = PullRequest(
            ref=ref,
            title=payload.get("title") or "",
            description=payload.get("body") or "",
            author=(payload.get("user") or {}).get("login") or "unknown",
            base_ref=(payload.get("base") or {}).get("ref") or "",
            head_ref=(payload.get("head") or {}).get("ref") or "",
            head_sha=(payload.get("head") or {}).get("sha") or "",
            state=payload.get("state") or "unknown",
        )
        pull_request.files = self.get_changed_files(ref)
        return pull_request

    def get_changed_files(self, ref: PullRequestRef) -> list[ChangedFile]:
        """List changed files with their unified-diff patches.

        The ``/files`` endpoint is used rather than the raw ``.diff`` media type
        because it already separates per-file status, counts and patch, removing
        the need to parse a multi-file diff header.
        """
        entries = self._paginate(f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/files")
        return [
            ChangedFile(
                path=entry.get("filename", ""),
                status=entry.get("status", "modified"),
                additions=int(entry.get("additions") or 0),
                deletions=int(entry.get("deletions") or 0),
                # Absent for binary files and for files too large for the API.
                patch=entry.get("patch"),
                previous_path=entry.get("previous_filename"),
            )
            for entry in entries
        ]

    def get_file_content(self, ref: PullRequestRef, path: str, sha: str) -> str | None:
        """Fetch a file's full text at a commit, or ``None`` if unavailable.

        Full content matters: a reviewer that sees only the changed hunk cannot
        tell whether a dependency is injected, whether an exception is handled
        further up, or whether the file already follows a local pattern.
        """
        url = f"/repos/{ref.owner}/{ref.repo}/contents/{quote(path)}"
        try:
            response = self._get(url, {"ref": sha})
        except GitHubError:
            return None

        payload = response.json()
        if isinstance(payload, list) or payload.get("encoding") != "base64":
            return None

        import base64

        try:
            raw = base64.b64decode(payload.get("content") or "")
            return raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None  # binary or non-UTF-8; the patch alone will have to do

    # ---- review history ---------------------------------------------------

    def get_review_comments(self, owner: str, repo: str, limit: int = 200) -> list[dict]:
        """Fetch recent review comments across the repository's pull requests.

        Uses the repo-wide ``/pulls/comments`` endpoint (newest first) so past
        review decisions can be indexed as tier-4 evidence without walking every
        pull request individually. Body-less and bot-authored comments are
        dropped — they carry no reviewable decision.
        """
        entries = self._paginate(
            f"/repos/{owner}/{repo}/pulls/comments",
            {"sort": "created", "direction": "desc"},
            max_items=limit,
        )

        comments: list[dict] = []
        for entry in entries:
            body = (entry.get("body") or "").strip()
            user = entry.get("user") or {}
            if not body or user.get("type") == "Bot":
                continue
            comments.append(
                {
                    "body": body,
                    "author": user.get("login") or "unknown",
                    "path": entry.get("path") or "",
                    "url": entry.get("html_url") or "",
                    "created_at": entry.get("created_at") or "",
                    "pr_number": _pr_number_from_url(entry.get("pull_request_url") or ""),
                }
            )
        return comments


def _pr_number_from_url(url: str) -> int:
    """Extract the PR number from an API ``pull_request_url``."""
    match = re.search(r"/pulls/(\d+)$", url)
    return int(match.group(1)) if match else 0
