# Performance Guidelines

Owned by the Platform team. These are review criteria, not optimisation targets to chase
speculatively — measure before restructuring anything not listed here.

## Data Access

- **No N+1 queries.** A query inside a loop over a result set is a defect. Batch it, or
  eager-load the relation in the original query.
- Every query that can return an unbounded result set must be paginated with a
  server-enforced maximum page size.
- Select only the columns you use. `SELECT *` in application code is a defect.
- Any column used in a `WHERE`, `JOIN` or `ORDER BY` of a hot query must be indexed; new
  query patterns require a migration adding the index in the same merge request.
- Write operations that touch many rows run in batches with an explicit transaction
  boundary, never row-by-row in a loop.

## Network Calls

- Never call a remote service inside a loop when a bulk endpoint exists.
- Every outbound call has a timeout, a bounded retry policy with exponential backoff and
  jitter, and a circuit breaker for critical dependencies. Unbounded retries amplify
  outages.
- Reuse HTTP clients and connection pools. Constructing a new client per request exhausts
  sockets.
- Sequential independent awaits are a defect: gather them concurrently
  (`asyncio.gather` / `Task.WhenAll`).

## Memory and Allocation

- Stream large payloads; never read an entire file or response body into memory when it can
  be processed incrementally.
- Do not materialise a full collection when an iterator/generator suffices.
- Avoid string concatenation in loops; use a builder or `join`.
- Caches must be bounded and have an eviction policy and TTL. An unbounded dictionary used
  as a cache is a memory leak.

## Algorithmic Cost

- Nested loops over two request-sized collections must be replaced with a hash lookup.
- Repeated linear scans of the same collection inside a loop indicate a missing index/dict.
- Do not sort inside a loop; sort once outside it.
- Work that does not depend on the loop variable must be hoisted out of the loop.

## Async Efficiency

- CPU-bound work must not run on the async event loop; it starves every other request.
- Blocking I/O inside an async function (a synchronous driver, `time.sleep`, blocking file
  read) defeats concurrency and is a defect.
- Background work must be bounded by a semaphore or queue; unbounded task creation under
  load exhausts the process.
