# Engineering Coding Standards

Version 3.1 — owned by the Architecture Guild. These rules are mandatory for all
services in the platform. Deviations require an ADR.

## Naming Conventions

- Modules and files use `snake_case` in Python and `PascalCase` in C#.
- Classes use `PascalCase`. Functions, methods and local variables use `snake_case`
  in Python and `camelCase` for locals / `PascalCase` for methods in C#.
- Boolean names must read as a predicate: `is_enabled`, `has_permission`,
  `should_retry`. Never `flag`, `status2`, `check`.
- Abbreviations are forbidden except for the approved list: `id`, `url`, `db`, `api`,
  `http`, `io`. Write `configuration`, not `cfg`; `repository`, not `repo`.
- Async functions must be suffixed with `_async` in Python and `Async` in C#.
- Collections are named in the plural (`users`), single items in the singular (`user`).
- Constants are `UPPER_SNAKE_CASE` and declared at module level, never inline.

## Functions and Complexity

- A function does one thing. Maximum 40 lines of body, maximum 4 parameters.
  Beyond 4 parameters, introduce a parameter object.
- Cyclomatic complexity must not exceed 10 per function.
- Nesting depth must not exceed 3 levels. Prefer early returns / guard clauses over
  `else` branches.
- No boolean parameters that switch behaviour (`do_work(force=True)`). Split into two
  named functions instead.

## Magic Values

- Literal numbers and strings that carry meaning must be named constants or enum members.
  `if status == 3` is a defect; `if status == OrderStatus.SHIPPED` is correct.
- Timeouts, retry counts, page sizes and limits are configuration, never literals in
  business logic.

## Error Handling

- Never swallow an exception. An `except`/`catch` block that logs nothing and rethrows
  nothing is a blocking review comment.
- Never catch the base exception type (`except Exception`, `catch (Exception)`) outside of
  a top-level request/job boundary. Catch the specific exceptions you can handle.
- Every caught exception must either be handled meaningfully, or wrapped in a
  domain-specific exception and rethrown with the original as the inner cause.
  In Python use `raise DomainError(...) from exc` — bare `raise DomainError(...)` inside an
  `except` block loses the original traceback.
- Do not use exceptions for control flow in expected paths (e.g. "not found").
  Return an explicit result/`Optional` for expected absence.
- Error messages must not contain secrets, tokens, connection strings, or personal data.
- Validate all external input at the boundary of the service and fail fast with a
  domain-specific validation error.

## Logging

- Use the module-level structured logger. `print()` / `Console.WriteLine` in application
  code is a defect.
- Log messages use structured fields, not string interpolation of variables:
  `logger.warning("retry scheduled", extra={"attempt": n})`.
- Never log request bodies, credentials, tokens, or personally identifiable information.
- Log at `ERROR` only for conditions requiring human action; expected failures are `WARNING`.

## Comments and Documentation

- Every public module, class and function has a docstring / XML doc describing purpose,
  parameters, return value and raised exceptions.
- Comments explain *why*, never *what*. A comment restating the code is noise and must
  be removed.
- No commented-out code in a merged change. Delete it; version control remembers.
- `TODO` comments must reference a tracking ticket: `# TODO(PLAT-1234): ...`.

## Dependency Management

- Business logic depends on abstractions (interfaces / protocols), never on concrete
  infrastructure classes. See ADR-001.
- Dependencies are injected through the constructor. Service locators, global singletons
  and module-level clients constructed at import time are forbidden — they break testing.
- No new third-party dependency without Architecture Guild approval recorded in an ADR.

## Async and Concurrency

- Never block an async call path with a synchronous wait (`.Result`, `.Wait()`,
  `asyncio.run` inside a running loop, `time.sleep` in a coroutine).
- Every outbound network call has an explicit timeout. There is no acceptable default.
- Shared mutable state must be protected by an explicit lock, or eliminated. Mutable
  module-level state shared across requests is a defect.
- `async` functions must not perform CPU-bound work; offload to a worker/thread pool.
- Always cancel or await spawned tasks; fire-and-forget tasks are forbidden because
  their exceptions are silently lost.

## API Consistency

- Public APIs return a consistent envelope; error responses use the shared `ProblemDetail`
  shape. Never leak a stack trace or internal exception message to a caller.
- Breaking changes to a public contract require a new version segment in the route.
- Field names in JSON payloads are `camelCase` and must match the published API contract.
- Pagination uses `page` and `pageSize` parameters with a server-enforced maximum.
