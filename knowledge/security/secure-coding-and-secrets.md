# Secure Coding and Secrets Handling

Owned by the Security team. Violations marked **BLOCKING** must not be merged.

## Secrets and Credentials

- **BLOCKING** — No secret may appear in source control: API keys, passwords, connection
  strings, private keys, bearer tokens, webhook signing secrets, or cloud access keys.
  This includes tests, fixtures, notebooks, sample configuration, and comments.
- All secrets are read from the environment or the platform secret store at runtime.
  Configuration files in the repository contain placeholders only.
- A `.env` file is always git-ignored. `.env.example` contains placeholder values only.
- **BLOCKING** — A committed secret is treated as compromised. Rotate it; removing the
  commit is not sufficient remediation.
- Secrets must never be written to logs, error messages, telemetry, or exception details.
- Do not pass secrets as command-line arguments; they are visible in the process table.

## Input Validation and Injection

- **BLOCKING** — All database access uses parameterised queries or the ORM's parameter
  binding. String concatenation or interpolation of user input into SQL is forbidden.
- Never pass user-controlled input to a shell, `eval`, `exec`, dynamic import, or a
  deserializer that can instantiate arbitrary types (`pickle`, `BinaryFormatter`).
- Validate and canonicalise any user-supplied path before file access; reject `..` segments
  to prevent path traversal.
- Validate the type, range, length and format of every field crossing a trust boundary.
  Allow-lists are required; deny-lists are insufficient.
- Outbound URLs derived from user input must be validated against an allow-list to prevent
  server-side request forgery.

## Authentication and Authorisation

- Every endpoint is authenticated by default; anonymous access is explicit and reviewed.
- Authorisation is checked on the server for every request, against the resource being
  accessed — not only on the route. Verify the caller owns the object they reference,
  otherwise the endpoint is vulnerable to insecure direct object reference.
- Never trust a client-supplied user id, role, or tenant id from the request body.
- Session tokens are rotated on privilege change and invalidated on logout.

## Cryptography

- Use the platform crypto library. Never hand-roll cryptography or invent a scheme.
- Passwords are hashed with `argon2id` or `bcrypt`. MD5 and SHA-1 are forbidden for any
  security purpose.
- Random values used for tokens, salts, or identifiers must come from a cryptographically
  secure generator (`secrets`, `RandomNumberGenerator`), never `random` / `Random`.
- TLS certificate validation must never be disabled, not even in tests against staging.

## Data Exposure

- API responses return only the fields the caller needs. Do not serialise a whole entity
  when the contract requires three fields.
- Personal data is not logged, and is redacted in error reports.
- Stack traces and internal exception messages are never returned to an external caller.

## Dependencies

- New dependencies must be pinned and scanned. Unpinned versions are rejected.
- Dependencies with known critical CVEs block the merge.
