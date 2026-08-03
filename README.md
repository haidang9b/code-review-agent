# AI Code Review Assistant — LangChain v1 & LangGraph

A code review assistant that reviews GitHub pull requests **against your own
documentation**. It reads coding standards, testing rules, security policy,
performance guidelines, ADRs and design documents from `.md`, `.pdf` and `.docx`
files, indexes your existing source code and past PR review comments, and then
reviews a pull request using only that retrieved evidence.

The review policy lives in [prompts/reviewer.md](prompts/reviewer.md). Its two
hardest rules — *never invent a project-specific rule* and *say so explicitly
when a rule cannot be verified* — are enforced by the pipeline, not just
requested in the prompt:

- Retrieval is **tiered**, so the model is shown which context is a mandatory
  standard and which is an incidental code sample.
- Every finding declares which tier it rests on, and a **verification node**
  checks that the rule it quotes really exists in the context that file was
  reviewed with. Unsupported claims are downgraded to *general best practice*
  and recorded as a documentation gap.

---

## Architecture

```
knowledge/**  (.md .pdf .docx)      REPO_PATH (local checkout)     GitHub API
      │ loaders.py                        │ code_loader.py              │ github_client.py
      │ splitters.py (prose/markdown)     │ splitters.py (by Language)  │
      ▼                                   ▼                             ▼
 Chroma "knowledge"                Chroma "project_code"         Chroma "review_history"
   tiers 1–2                          tier 3                        tier 4
      └──────────────┬──────────────────────┴─────────────────────────────┘
                     ▼  retriever.py — tiered, priority-ordered retrieval
        graph.py  StateGraph:
          fetch_pull_request → plan_review → retrieve_context
              → review_files → verify_findings → assemble_report
                     ▼
        outputs/pr-<n>-review.md  +  outputs/pr-<n>-review.json
```

| Node | What it does |
|---|---|
| `fetch_pull_request` | PR metadata, changed files with patches, and file context at the head commit. Skips deleted/binary/generated files and records every skip. |
| `plan_review` | Deterministic (no model call): detects language, extracts changed symbols, and renders the diff with real new-file line numbers. |
| `retrieve_context` | Per file, retrieves each priority tier separately with its own budget. One batched embedding request per file. |
| `review_files` | One `with_structured_output(FileReview)` call per file, with tier-labelled context. |
| `verify_findings` | The groundedness gate. Verbatim quotes pass for free; anything else is checked by a strict verifier. Unsupported project-rule claims are downgraded, not deleted. |
| `assemble_report` | Sorts by severity and renders Markdown + JSON, including unverified concerns, skipped files, and every source consulted. |

**Modern patterns used**

| Requirement | Implementation |
|---|---|
| Structured output | `llm.with_structured_output(FileReview)` — no manual JSON parsing |
| Workflow | LangGraph `StateGraph` with a conditional edge — no `AgentExecutor` |
| Document loading | `pypdf` and `python-docx` directly — avoids `langchain-community` |
| Chunking | `MarkdownHeaderTextSplitter` for docs, `from_language` for code |
| Vector store | Three Chroma collections in one persisted directory |

---

## Context priority tiers

The reviewer prompt mandates an ordering of context sources. It is implemented as
four separately-retrieved tiers, with the folder a document sits in deciding its
tier — never an LLM guess.

| Tier | Meaning | Comes from |
|---|---|---|
| 1 | Coding Standards | `knowledge/standards/`, `testing/`, `security/`, `performance/`, `conventions/` |
| 2 | Architecture Rules | `knowledge/architecture/`, `patterns/`, `adr/`, `design/`, `api/` |
| 3 | Existing Project Patterns | your source code, via `ingest-code` |
| 4 | Previous Review Decisions | past PR comments, via `ingest-reviews` |
| 5 | General Best Practices | the model's own knowledge — never retrieved, and always labelled as such in the report |

A file dropped into an unrecognised folder still gets indexed (as `other`, at
tier 1) rather than being silently ignored.

---

## How to run

### Quickstart — zero to a review in five commands

Run these from the project root (`d:\Learning\Langchain\ai-code-review`) in
PowerShell. This exact sequence is what the project was verified with.

```powershell
# 1. Create the virtual environment and install dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Configure credentials (then open .env and fill in your Azure values)
Copy-Item .env.example .env

# 3. Index your documentation — coding standards, ADRs, design docs (tiers 1–2)
python -m src.cli ingest-docs

# 4. Index your source code so "existing project patterns" is real (tier 3)
python -m src.cli ingest-code --repo D:\work\my-service

# 5. Review a pull request
python -m src.cli review --pr https://github.com/myorg/my-service/pull/123
```

The report is written to `outputs/pr-123-review.md` and `outputs/pr-123-review.json`.

**Minimum to run:** the five `AZURE_OPENAI_*` variables in `.env`, plus at least
one ingest. Steps 4 and 5 need nothing else for a public repository — a
`GITHUB_TOKEN` is only needed for private repos, for `ingest-reviews`, or to lift
the 60-requests/hour anonymous rate limit.

> **In this checkout, steps 1–4 are already done.** `.venv/` exists with
> dependencies installed, `.env` holds working Azure values, and the sample
> standards plus a code corpus are already indexed — check with
> `python -m src.cli status`. To see a real review immediately:
>
> ```powershell
> .\.venv\Scripts\Activate.ps1
> python -m src.cli review --pr psf/requests#7589
> ```
>
> That is a real public pull request, and the resulting report is already in
> `outputs/` if you want to read one before running anything.

### Shell notes

| Shell | Activate the venv |
|---|---|
| PowerShell | `.\.venv\Scripts\Activate.ps1` |
| cmd.exe | `.venv\Scripts\activate.bat` |
| Git Bash / macOS / Linux | `source .venv/bin/activate` |

If PowerShell blocks the activation script, either allow it once with
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, or skip activation
entirely and call the interpreter directly — this works in any shell:

```powershell
.\.venv\Scripts\python.exe -m src.cli status
```

All commands below are written as `python -m src.cli …` and assume the venv is
active. Run them from the project root — `src` is imported as a package, so a
different working directory fails with `No module named 'src'`.

### Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | e.g. `https://my-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | default chat deployment |
| `AZURE_OPENAI_API_VERSION` | e.g. `2024-10-21` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | embeddings deployment |
| `AZURE_OPENAI_REVIEW_DEPLOYMENT` | *optional* — stronger deployment used only for the review and verification calls |
| `GITHUB_TOKEN` | *optional* — required for private repos and for `ingest-reviews`; public PRs work unauthenticated at 60 req/hour |
| `GITHUB_API_URL` | *optional* — GitHub Enterprise base, e.g. `https://github.mycompany.com/api/v3` |
| `REPO_PATH` | local checkout to index as tier 3 |
| `KNOWLEDGE_PATH` | knowledge folder (default `./knowledge`) |
| `MAX_FILES_PER_REVIEW` | per-review file cap (default 25) |
| `MAX_PATCH_CHARS` | per-file diff cap (default 12000) |
| `LANGSMITH_API_KEY` | *optional* — tracing turns on automatically when present |

> **Model choice matters here.** A nano/mini deployment is fine for embeddings
> and cheap enough for ingestion, but code review is the wrong place to economise:
> set `AZURE_OPENAI_REVIEW_DEPLOYMENT` to your strongest available deployment.
> Findings quality and the reliability of the verification gate both depend on it.

---

## Commands

Every command is a subcommand of `python -m src.cli`. Run `python -m src.cli -h`,
or `python -m src.cli <command> -h`, for the built-in help.

### `ingest-docs` — index your documentation (tiers 1–2)

```powershell
python -m src.cli ingest-docs                       # uses KNOWLEDGE_PATH or ./knowledge
python -m src.cli ingest-docs --path D:\docs\eng     # index a different folder
python -m src.cli ingest-docs --force                # rebuild from scratch
```

First, drop real documents into `knowledge/`, using the **subfolder name to
declare what each document is** — that is what assigns its priority tier:

```
knowledge/
├── standards/      coding-standards.md, naming.docx, style-guide.pdf   → tier 1
├── testing/        testing-standards.md                                → tier 1
├── security/       secure-coding-and-secrets.pdf                       → tier 1
├── performance/    performance-guidelines.md                           → tier 1
├── architecture/   adr-001-....md, system-design.docx                  → tier 2
└── patterns/       design-patterns.md                                  → tier 2
```

`.md`, `.pdf` and `.docx` are all supported, mixed freely in the same folder.
Output looks like this:

```
Ingesting knowledge documents (.md / .pdf / .docx)…
  embedded 40/40 chunks
Collection 'knowledge' ← D:\Learning\Langchain\ai-code-review\knowledge
  documents: 6   chunks: 40   indexed: 40
  breakdown: architecture=5  patterns=5  performance=6  security=7  standards=10  testing=7
```

The repository ships sample `.md` standards so the pipeline runs immediately —
**replace them with your own**, or every finding will come back as *general best
practice*. `.pdf` and `.docx` files are git-ignored so internal documents are not
committed by accident.

### `ingest-code` — index your source code (tier 3)

```powershell
python -m src.cli ingest-code --repo D:\work\my-service
python -m src.cli ingest-code                 # uses REPO_PATH from .env
python -m src.cli ingest-code --force
```

Point it at a **local checkout**. This is what lets the reviewer say "the rest of
this codebase does it this way" instead of guessing. Dependency and build folders
(`node_modules`, `.venv`, `bin`, `obj`, `dist`, …) and generated or minified files
are skipped automatically.

Re-run it after the branch you index moves on — the index is a snapshot.

### `ingest-reviews` — index past review decisions (tier 4)

```powershell
python -m src.cli ingest-reviews --repo myorg/my-service
python -m src.cli ingest-reviews --repo myorg/my-service --limit 500 --force
```

Pulls the most recent human review comments from the repository (bot comments are
dropped) so decisions your team already made become citable evidence. Needs a
`GITHUB_TOKEN` for private repositories.

### `status` — what is actually indexed

```powershell
python -m src.cli status
```

```
Vector store: D:\Learning\Langchain\ai-code-review\.chroma
  knowledge            40 chunks   (tier 1–2  coding standards / architecture)
  project_code        118 chunks   (tier 3    existing project patterns)
  review_history       30 chunks   (tier 4    previous review decisions)
```

Run this first whenever a review's findings look thin — an empty `knowledge`
collection means there is no project rule available to cite.

### `review` — review a pull request

```powershell
# any of these PR forms work
python -m src.cli review --pr https://github.com/myorg/my-service/pull/123
python -m src.cli review --pr myorg/my-service#123
python -m src.cli review --pr myorg/my-service/pull/123

# options
python -m src.cli review --pr myorg/repo#123 --out reports      # write elsewhere
python -m src.cli review --pr myorg/repo#123 --format md        # skip the JSON
python -m src.cli review --pr myorg/repo#123 --fail-on High     # non-zero exit
```

Prints a summary and writes `outputs/pr-<n>-review.md` and `.json`. **Nothing is
ever posted back to GitHub** — the token is only ever used to read.

```
psf/requests PR #7589 — 5 file(s) reviewed
  findings: 3 (1 High, 1 Medium, 1 Low)
    [High/Bug] src/requests/utils.py:932 — `resolve_proxies` now calls `getproxies()` …
    [Medium/Bug] src/requests/_internal_utils.py:46 — `unicode_is_ascii` now relies on …
    [Low/Maintainability] src/requests/models.py:900 — The literal `32768` is a magic value …
  unverified concerns: 5
  skipped / partial: 4
  context sources cited: 32
Report written to D:\…\outputs\pr-7589-review.md
Report written to D:\…\outputs\pr-7589-review.json
```

**Exit codes**

| Code | Meaning |
|---|---|
| `0` | review completed |
| `1` | expected, actionable failure (bad config, PR not found, rate limit) — message only, no traceback |
| `2` | `--fail-on <severity>` was given and a finding met or exceeded it |

A review costs roughly one model call per changed file, plus one per finding that
claims a project rule but did not quote it verbatim. `MAX_FILES_PER_REVIEW`
(default 25) is the ceiling on the first number.

### `search` — see what retrieval actually finds

The fastest way to tell "the reviewer missed our rule" from "our rule is not
indexed":

```powershell
python -m src.cli search "error handling rules" --tier 1
python -m src.cli search "dependency direction between layers" --tier 2 -k 3
python -m src.cli search "how do we load settings" --tier 3
python -m src.cli search "missing tests for new logic" --tier 4
```

### `export-graph` — the workflow diagram

```powershell
python -m src.cli export-graph                     # outputs/graph.png
python -m src.cli export-graph --out docs\flow.png
```

Falls back to an ASCII rendering if PNG rendering is unavailable.

### Running the graph directly

```powershell
python -m src.graph https://github.com/myorg/repo/pull/123   # prints the raw JSON report
```

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Error: Required environment variable 'AZURE_OPENAI_API_KEY' is not set.` | No `.env` in the project root, or it is missing a value. Copy `.env.example` to `.env` and fill in the five `AZURE_OPENAI_*` variables. |
| `No module named 'src'` | The working directory is not the project root, so the `src` package cannot be imported. `cd` to the project root and re-run. (`.env` itself is found from the project root regardless of where you run from.) |
| `Error: Not found: …/pulls/123` | Wrong owner/repo/number, or a private repo without a token. Set `GITHUB_TOKEN`. |
| `GitHub returned 403 … rate limit exhausted` | 60 anonymous requests/hour is used up. Set `GITHUB_TOKEN` to get 5000/hour. |
| `Nothing was indexed` from `ingest-docs` | No supported files under the knowledge folder. Add `.md`/`.pdf`/`.docx` files inside a subfolder — files loose in the root are indexed as `other`. |
| `Error: No repository path given.` | `ingest-code` needs `--repo <path>` or `REPO_PATH` in `.env`. |
| Every finding says *General Best Practice* | The relevant rule is not in the index. Check with `status`, then `search` for the rule. This is the verification gate working, not a bug. |
| `Warning: no corpus has been ingested` | You ran `review` before any ingest. The review still runs, but nothing can be cited. |
| A rule you have documented is never cited | Confirm it is retrievable: `python -m src.cli search "<the rule>" --tier 1`. If it does not come back, the document may be scanned-image PDF with no extractable text — `ingest-docs` reports those under `problems`. |
| LangSmith 401 errors flooding the output | A stale `LANGSMITH_TRACING=true` with no key. Remove `LANGSMITH_TRACING` from `.env`; it is managed automatically from `LANGSMITH_API_KEY`. |

---

## What a report contains

- **Summary** — finding counts by severity, or the exact sentence
  *"The code follows the available coding standards and no significant issues
  were identified."* when nothing was found. No issues are invented to fill space.
- **Findings** — category, severity, `file:line`, the tier the finding rests on,
  the source document, why it matters, quoted evidence, and a recommendation.
- **What the change does well** — good practices, with the standard they match.
- **Rules that could not be verified** — concerns relevant to the change that no
  retrieved document covers, plus any finding the verification gate downgraded.
  This section is the honest answer to "did you check X?".
- **Files skipped or only partially reviewed** — deleted, binary, generated and
  over-limit files, and files whose context was excerpted rather than sent whole.
- **Context consulted** — every source that could have been cited.

---

## Layout

```
src/
├── config.py         settings, paths, tier definitions
├── llm.py            Azure chat + embedding model factories
├── loaders.py        .md / .pdf / .docx → Documents (doc_type from folder)
├── code_loader.py    local checkout → Documents (language detection, ignore rules)
├── splitters.py      markdown-header / prose / language-aware chunking
├── vector_store.py   three Chroma collections, deterministic upsert ids
├── retriever.py      tiered retrieval, context formatting, citations
├── github_client.py  read-only GitHub REST client
├── diff.py           patch parsing, real line numbers, hunk-window excerpts
├── schemas.py        Pydantic structured-output models
├── prompts.py        loads prompts/reviewer.md, assembles messages
├── graph.py          the StateGraph pipeline
├── report.py         Markdown + JSON rendering
├── ingest.py         corpus ingestion for all three collections
└── cli.py            argparse entry point
```

## Limitations

- Reviews GitHub pull requests only; there is no local-diff or paste input.
- Read-only: it does not post comments or reviews back to GitHub.
- Tier 3 is indexed from a **local checkout**, so re-run `ingest-code` after the
  branch you index moves on.
- The reviewer sees changed hunks plus surrounding file context, not the whole
  repository, so a cross-file architectural break is only caught when the
  relevant rule or existing pattern is retrievable.
