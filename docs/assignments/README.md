# Developer assignments

Each developer owns ONE non-functional requirement and applies it across
every relevant feature module. NFR ownership is global; feature ownership
is shared.

| Dev | NFR | Sheet |
|---|---|---|
| Dev 1 | Concurrent access (NFR1) | [dev1-concurrent-access.md](dev1-concurrent-access.md) |
| Dev 2 | Resource management (NFR2) | [dev2-resource-management.md](dev2-resource-management.md) |
| Dev 3 | Async queues (NFR3) | [dev3-async-queues.md](dev3-async-queues.md) |
| Dev 4 | Batch processing (NFR4) | [dev4-batch-processing.md](dev4-batch-processing.md) |
| Dev 5 | Load distribution (NFR5) | [dev5-load-distribution.md](dev5-load-distribution.md) |
| Dev 6 | Distributed caching (NFR6) | [dev6-distributed-caching.md](dev6-distributed-caching.md) |
| Dev 7 | Concurrency control (NFR7) | [dev7-concurrency-control.md](dev7-concurrency-control.md) |
| Dev 8 | ACID transactions (NFR8) | [dev8-acid-transactions.md](dev8-acid-transactions.md) |
| Dev 9 | Stress testing (NFR9) | [dev9-stress-testing.md](dev9-stress-testing.md) |
| Dev 10 | Benchmarking (NFR10) | [dev10-benchmarking.md](dev10-benchmarking.md) |

NFR1–5 are implemented (code + reports in [../reports/](../reports/)).
NFR6–10 now have specs ([../requirements/](../requirements/)), assignment
sheets (above), and code stubs ready — but are **not yet implemented**.
Each NFR6–10 owner writes their implementation report under
`../reports/<n>-nfr<n>-implementation.md` **after** the work is done, with
real before/after numbers (do not pre-fill it).

## Working agreement

- Each Dev branches off `main` as `feat/nfr<n>-<owner>`.
- Pull requests must update both:
  - the corresponding `docs/requirements/<n>-*.md` (what changed and why),
  - `docs/CONCURRENCY_POINTS.md` (if any concurrency point is added or
    altered).
- Review by at least one other Dev whose NFR overlaps. Strong overlap
  pairs:
  - Dev 1 ↔ Dev 7 (Concurrent access ↔ Locking strategies)
  - Dev 1 ↔ Dev 8 (Concurrent access ↔ ACID)
  - Dev 3 ↔ Dev 4 (Async ↔ Batch — both touch Celery)
  - Dev 5 ↔ Dev 6 (Load balancing ↔ Distributed cache)
  - Dev 9 ↔ Dev 10 (Stress test ↔ Benchmarking — feed each other)
