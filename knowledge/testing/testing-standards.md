# Testing Standards

Owned by the QA Guild. Applies to every merge request.

## Coverage Requirements

- Every new public function or method in the `domain` and `application` layers ships with
  unit tests in the same merge request. A change that adds business logic with no test is
  blocked.
- Bug fixes ship with a regression test that fails before the fix and passes after it.
- Line coverage must not decrease. New domain code targets 85% line coverage.
- Pure refactors with no behaviour change do not require new tests, but existing tests
  must not be modified to make the refactor pass.

## Test Structure

- One test file per production file, named `test_<module>.py` / `<Class>Tests.cs`, mirroring
  the source folder structure.
- Test names describe behaviour: `test_transfer_rejects_insufficient_balance`, not
  `test_transfer_2`.
- Follow Arrange / Act / Assert with a blank line between the three sections.
- One logical assertion per test. Use parameterised tests for input variations rather than
  looping inside a single test.

## What to Test

- The happy path, every documented error path, and the boundary of every validated range.
- Null / empty / whitespace inputs for anything accepting a string.
- Concurrency-sensitive code needs a test that exercises the contended path.

## Test Isolation

- Unit tests must not touch the network, the file system, the clock, or a database.
- Time must be injected (a clock abstraction). `DateTime.Now` / `datetime.now()` called
  directly inside business logic is untestable and is a defect — inject the clock.
- Randomness must be injected or seeded for the same reason.
- Shared mutable fixtures between tests are forbidden; tests must pass in any order and
  in parallel.
- Integration tests are separated into their own suite and marked so they can be excluded
  from the fast feedback loop.

## Test Doubles

- Prefer real objects, then fakes, then stubs. Mock only what you own and cannot fake.
- Do not assert on the internal call sequence of a collaborator unless the ordering is part
  of the contract; such tests break on every refactor.
- Never mock the class under test.

## Testability Smells That Block Review

- Static/global state, hidden singletons, or a class constructing its own dependencies —
  these make substitution impossible. Inject through the constructor (see coding standards).
- Private methods that need direct testing indicate a missing collaborator; extract a class.
- A function that both computes and performs I/O cannot be tested without I/O; split the
  computation out as a pure function.
