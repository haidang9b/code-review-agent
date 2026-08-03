# Approved Design Patterns and SOLID Expectations

Owned by the Architecture Guild. Companion to ADR-001.

## SOLID Expectations in Review

- **Single Responsibility** — a class has one reason to change. A class that validates,
  persists and notifies is three classes. Names containing `Manager`, `Helper`, `Utils` or
  `Processor` usually signal a missing responsibility boundary and must be justified.
- **Open/Closed** — behaviour that varies by type must not be selected with a growing
  `if`/`switch` on a type code. Use polymorphism or the strategy pattern.
- **Liskov Substitution** — a subclass must not strengthen preconditions or throw
  `NotImplementedException` for an inherited member. If it cannot fulfil the contract, it
  is not a subtype.
- **Interface Segregation** — interfaces stay small and client-specific. A consumer must not
  be forced to depend on methods it never calls.
- **Dependency Inversion** — high-level modules depend on abstractions owned by the domain
  layer, never on infrastructure classes (see ADR-001).

## Approved Patterns

| Pattern | Use for | Notes |
|---|---|---|
| Repository | Aggregate persistence | Interface in `domain`, implementation in `infrastructure`. One repository per aggregate root, not per table. |
| Unit of Work | Transaction boundary per use case | Owned by the application layer. |
| Strategy | Behaviour that varies by input or configuration | Replaces type-code switches. |
| Factory / Factory Method | Non-trivial object construction | Keeps constructors free of branching. |
| Decorator | Cross-cutting concerns (caching, retry, logging) | Preferred over editing the wrapped class. |
| Adapter | Wrapping a third-party SDK | Keeps vendor types out of `domain`. |
| Options / Parameter Object | More than 4 parameters | Required by the coding standards. |
| Result object | Expected failure outcomes | Preferred over exceptions for expected paths. |

## Discouraged Patterns

- **Singleton** for anything holding mutable state — it breaks test isolation and hides
  dependencies. Use a single registration in the DI container instead.
- **Service Locator** — hides dependencies from the constructor signature. Forbidden.
- **Anemic domain model** — entities that are pure data bags with all rules in a service
  layer. Behaviour belongs with the data it guards.
- **God object / God service** — a class over roughly 400 lines or with more than 7
  dependencies must be decomposed.
- **Inheritance for code reuse** — prefer composition. Inheritance is for substitutability.
- **Generic `Utils` modules** — place the function next to the concept it serves.

## Common Refactorings Requested in Review

- Replace conditional with polymorphism when the same `switch` on a type appears twice.
- Extract method when a function exceeds the complexity limit in the coding standards.
- Introduce a parameter object when a parameter list grows past four.
- Wrap a third-party client in an adapter the first time a second call site appears.
