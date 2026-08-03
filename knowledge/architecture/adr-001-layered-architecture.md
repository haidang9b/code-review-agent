# ADR-001: Layered Architecture and Dependency Direction

- Status: Accepted
- Date: 2024-03-11
- Deciders: Architecture Guild

## Context

Services in the platform had grown mixed responsibilities: HTTP controllers issued SQL
directly, and domain classes imported the ORM. This made unit testing impossible without a
database and coupled business rules to infrastructure choices.

## Decision

Every service is organised into four layers, and dependencies point inward only:

```
api  →  application  →  domain  ←  infrastructure
```

- **api** — controllers/endpoints. Translates transport to application commands. Contains no
  business rules and no data access.
- **application** — use cases, orchestration, transactions. Depends on domain abstractions.
- **domain** — entities, value objects, domain services, and the repository *interfaces*.
  Depends on nothing outside the domain layer. No ORM types, no HTTP types, no framework
  attributes.
- **infrastructure** — implementations of domain interfaces: ORM repositories, HTTP clients,
  message bus, cache. Depended upon by nobody; wired in at composition root.

## Rules

1. The domain layer must not import from `api`, `application`, or `infrastructure`.
2. Controllers must not reference a repository or a database session directly. They call an
   application service.
3. Data access happens only in `infrastructure`. Raw SQL outside `infrastructure` is a
   violation.
4. Persistence entities must not be returned from an API endpoint. Map to a DTO at the
   application boundary; leaking a persistence model couples the public contract to the
   schema.
5. Cross-service calls go through a client abstraction defined in `domain` and implemented
   in `infrastructure`.
6. Transactions are opened in the application layer, one per use case. Never in the domain
   layer, never in a repository method that is composed with others.
7. New layer-crossing exceptions require an amendment to this ADR.

## Consequences

- Domain logic is unit-testable with no database or HTTP server.
- Swapping an infrastructure implementation does not touch business rules.
- More mapping code between layers, accepted deliberately as the cost of isolation.
