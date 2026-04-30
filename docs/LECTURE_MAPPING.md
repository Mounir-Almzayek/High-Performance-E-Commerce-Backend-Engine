# Lecture → project mapping

This document explicitly ties every concept covered in Sessions 1–4 of
the Parallel Programming course to the part of this project that
implements (or relies on) it. Reviewers can use this as a checklist when
grading: every lecture term should be locatable in the codebase.

---

## Session 1 — Concurrent Access & Thread Safety

Mapped to **NFR1** (concurrent access) and **NFR7** (locking strategies).

| Lecture concept | Where it lives in the project |
|---|---|
| Concurrency vs. parallelism | `docs/ARCHITECTURE.md` § 5 (three concurrency tiers) |
| Shared resources | `apps/inventory/models.py::StockItem.on_hand / reserved`, `apps/users/models.py::Customer.loyalty_points` |
| Race condition (read-modify-write) | The exact "Bank Account Problem" pattern is documented in [requirements/01-concurrent-access.md](requirements/01-concurrent-access.md) using `reserve_stock` as the running example |
| Lost update | `apps/inventory/services.py::reserve_stock` — protected by `select_for_update` (pessimistic) **or** `version` CAS (optimistic) |
| Thread safety | `core/concurrency/locks.py` — every public service function in `apps/*/services.py` MUST go through these helpers |
| Mutex / synchronization | Postgres `SELECT ... FOR UPDATE` (DB-level mutex) + Redis `SET NX PX` (cross-instance mutex) — both in `core/concurrency/locks.py` |
| Acquire / Process / Release | `distributed_lock(key)` is a context manager that enforces the lifecycle — `__enter__` acquires, `__exit__` releases via Lua compare-and-delete |
| Deadlock — circular wait | Avoided in `apps/inventory/services.py::bulk_reserve` by **acquiring locks in ascending PK order**. Documented in [CONCURRENCY_POINTS.md](CONCURRENCY_POINTS.md) § 8 as the project-wide rule |
| Critical section size | All services keep work outside the locked region wherever possible: validation and total computation happen first, lock is acquired for the actual update only |
| Immutable data (lock-avoidance) | `OrderItem` snapshots `product_sku`, `product_name`, `unit_price` at order time so subsequent product mutations cannot retroactively change historical orders |

**Demo artefact**: a unit test under `tests/unit/test_concurrency_inventory.py`
that spawns N threads racing for one unit of stock and asserts exactly one
succeeds — this is the literal "two users, one $60 withdrawal" scenario
from the lecture.

---

## Session 2 — Advanced Thread Management & Thread Pools

Mapped to **NFR2** (resource management).

| Lecture concept | Where it lives in the project |
|---|---|
| Thread lifecycle / overhead of `new` thread per task | Justifies why we use a pool, not a fresh thread per request — see [requirements/02-resource-management.md](requirements/02-resource-management.md) |
| Thread pool (worker pool model) | Two-tier in this project: **outer** = Gunicorn workers (process pool); **inner** = `core.resources.bounded_executor` (thread pool inside one request) |
| Task queue feeding worker threads | Celery's broker queue (Redis) feeding `celery_worker` is the same model at the inter-service level; see `tasks/__init__.py` |
| Fixed-size pool | Gunicorn `--workers $GUNICORN_WORKERS` and Celery `--concurrency $CELERY_CONCURRENCY` — both fixed, both env-tunable |
| Cached pool | Not used — predictability of fixed pool wins for DB connection budgeting |
| Scheduled pool | Celery beat schedule in `tasks/__init__.py` (`daily-sales-batch`, `warm-product-cache`) |
| CPU-bound sizing rule `N + 1` | Documented in [requirements/02-resource-management.md](requirements/02-resource-management.md) for the Locust `BrowseOnly` scenario where the cache layer makes traffic CPU-bound on Django |
| I/O-bound sizing rule `N * (1 + W/C)` | Documented for the checkout flow which is dominated by DB wait — drives our Gunicorn `--threads` choice |
| Graceful shutdown | `gunicorn ... --timeout` + Celery's SIGTERM handling. `core.resources.acquire_slot(timeout=...)` returns False fast under saturation rather than queuing forever |
| Exception handling in pool tasks | Celery: `autoretry_for`, `retry_backoff`, `retry_kwargs={"max_retries": ...}` set per task in `tasks/notifications.py` and `tasks/invoicing.py` |
| Pool monitoring (active threads / queue depth) | Diagnostic endpoint `GET /api/v1/_diag/pool/` (NFR2 owner) returning `core.resources.get_pool_stats()`; Flower exposes Celery's view |
| Naming threads for debuggability | `bounded_executor(name_prefix=...)` — explicit recommendation in `core/resources/pool.py` docstring |

**Demo artefact**: NFR2 report compares two `GUNICORN_WORKERS` settings on
the same Locust scenario — the "with and without thread pool" graph from
the lecture, but on real numbers.

---

## Session 3 — Messaging Queues & Asynchronous Processing

Mapped to **NFR3** (asynchronous queues).

| Lecture concept | Where it lives in the project |
|---|---|
| Synchronous vs. asynchronous | The classic "8-second checkout" example IS the design rationale: `apps/orders/services.py::place_order` returns immediately, while invoice + email run from `tasks/invoicing.py` and `tasks/notifications.py` |
| Producer / queue / consumer | Producer = Django view; queue = Redis (Celery broker); consumer = `celery_worker` |
| Decoupling | Producer never imports the consumer — both depend only on the task name registered with Celery |
| Scalability via more consumers | `docker-compose.yml::celery_worker.deploy.replicas` (or simply `--scale celery_worker=N`) |
| Fault tolerance — messages survive crashes | `CELERY_TASK_ACKS_LATE = True` in `config/settings/base.py` ensures the message is only ACKed after successful execution |
| Spiky traffic absorption | The queue is the buffer. The 100-VU spike in NFR9 lands cleanly because the heavy work is queued, not blocking the request |
| Point-to-point (P2P) | Default Celery routing — one consumer per message |
| Pub/Sub | Future extension via Celery's exchange topology; not needed today but the architecture admits it |
| RabbitMQ vs. Kafka vs. SQS | `requirements/03-async-queues.md` notes our choice (Redis + Celery) and what would push us to RabbitMQ (complex routing) or Kafka (event sourcing) |
| Retry with exponential backoff | `retry_backoff=True` per task — Celery handles the doubling automatically |
| Dead Letter Queue (DLQ) | Configured via `task.reject(requeue=False)` after `max_retries`. NFR3 owner deliverable: a DLQ inspection endpoint or admin view |
| Idempotency (designing for at-least-once delivery) | `tasks/notifications.py::send_order_confirmation` documented as MUST be idempotent; suggested guard: an `OrderEmailDispatch` table or Redis SETNX |
| Monitor consumer lag / queue depth | Flower at `:5555` — required to be shown during NFR3 demo |
| Keep messages small (send IDs, not blobs) | Project convention enforced via the lint rule "tasks accept IDs, not model instances" — written into `docs/DEVELOPER_GUIDE.md` § 4 |
| Poison pill handling | DLQ + structured log of `audit.fail` from `@audit_log` decorator — every failed task lands in both |

**Demo artefact**: Flower screenshot showing a successful retry chain on
an injected failure, plus a kill-and-restart of `celery_worker` mid-task
producing zero duplicate emails (idempotency proof).

---

## Session 4 — Batch Processing in Parallel Environments

Mapped to **NFR4** (batch processing).

| Lecture concept | Where it lives in the project |
|---|---|
| Definition of batch processing | `tasks/daily_sales_batch.py` is the textbook example: scheduled, automated, high-throughput, off-peak |
| Batch vs. real-time trade-off | Daily aggregation is batch; checkout is real-time. Both share the same code paths via `apps/orders/models.py`, but live on different schedules |
| Partition / distribute / execute | The chunked-parallel model in `core/batch/process_in_parallel`: partition (chunks via `iterator(chunk_size=...)`), distribute (submit to bounded executor), execute (parallel handlers), merge (`DailySalesAggregator.merge`) |
| Sequential vs. parallel — the bank-statement "115 days vs. 27 hours" example | Reproduced in microcosm by NFR4 report: serial vs. parallel runtime on the seeded 2k-orders / 6k-items dataset |
| ETL (Extract / Transform / Load) | Extract = `OrderItem.objects.filter(...).iterator()`. Transform = `_aggregate_chunk`. Load = `DailySalesReport` row insert |
| Fixed-size chunking | Default in `core/batch/chunked.py::DEFAULT_CHUNK_SIZE = 1000`, tunable per call |
| Dynamic partitioning | NFR4 stretch goal: chunk size adapted by `bounded_executor` queue depth — described in [requirements/04-batch-processing.md](requirements/04-batch-processing.md) § "Stretch" |
| Stragglers | Accepted as a documented risk for fixed chunking. Mitigated by sizing chunks small relative to total work and reporting per-chunk timing |
| Resource limits — don't add more threads | NFR4 explicitly uses `core.resources.bounded_executor` so the batch cannot starve foreground HTTP traffic for DB connections |
| Idempotent retries | `run_daily_sales` deletes the prior `DailySalesReport` row for the window before persisting — re-running the job for the same day is safe |
| Checkpointing | Per-chunk persistence after each merge so a mid-run crash resumes from the last successful chunk; documented in [requirements/04-batch-processing.md](requirements/04-batch-processing.md) |
| Partial failure: skip vs. stop | Choice belongs to NFR4 owner. Default = SKIP (log + dead-letter the chunk, continue), because data quality issues should not abort an overnight run |
| Detailed logging per chunk | `@timed("daily_sales_batch.chunk")` decorator on `_aggregate_chunk` plus chunk index in every log line |
| Memory bound | `qs.iterator(chunk_size=N)` keeps RSS flat regardless of dataset size — proven in NFR4 report |

**Demo artefact**: NFR4 report shows runtime for `(chunk_size, max_workers)` pairs
`(500, 2)`, `(500, 8)`, `(2000, 2)`, `(2000, 8)` on the seeded dataset, plus
a flat-line memory profile.

---

## Quick lookup — concept to file

| Concept | File |
|---|---|
| Race condition | `core/concurrency/locks.py`, `apps/inventory/services.py` |
| Mutex / lock | `core/concurrency/locks.py::distributed_lock` |
| Optimistic lock | `core/concurrency/locks.py::bump_version`, `version` field on every contended model |
| Pessimistic lock | `core/concurrency/locks.py::select_for_update_or_skip` |
| Deadlock avoidance | `apps/inventory/services.py::bulk_reserve` (PK ASC ordering) |
| Thread pool (process) | `docker/entrypoint.sh` (Gunicorn workers) |
| Thread pool (in-process) | `core/resources/pool.py::bounded_executor` |
| Pool sizing rules | `docs/requirements/02-resource-management.md` |
| Producer / queue / consumer | `apps/orders/services.py` (producer), Redis (queue), `tasks/*.py` (consumers) |
| Retries + DLQ | `tasks/notifications.py`, `tasks/invoicing.py` |
| Idempotency | `tasks/notifications.py` (email dispatch table), `apps/payments/models.py::WebhookEvent.signature` UNIQUE |
| ETL pipeline | `tasks/daily_sales_batch.py` |
| Chunking | `core/batch/chunked.py::iter_in_chunks` |
| Parallel batch | `core/batch/chunked.py::process_in_parallel` |
| Checkpointing | `tasks/daily_sales_batch.py` (NFR4 owner deliverable) |
