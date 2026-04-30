# NFR2 — Resource management

> Owner: **Dev 2**
> Status: stubs ready in `core/resources/pool.py`, env knobs wired in
> `docker/entrypoint.sh` and `.env.example`.

## Objective

Cap the amount of computational work the system performs in parallel so
it never crashes under load **and** never under-utilizes capacity to the
point of slowness.

This is a **two-knob problem** and both knobs must be tuned together.

## Two layers of concurrency

### Outer cap — Gunicorn / Celery

Defined in `docker/entrypoint.sh`:

```
gunicorn ... --workers $GUNICORN_WORKERS --threads $GUNICORN_THREADS
celery worker ... --concurrency $CELERY_CONCURRENCY
```

| Variable | Default | Purpose |
|---|---|---|
| `GUNICORN_WORKERS` | 4 | Number of forked OS workers per Django instance |
| `GUNICORN_THREADS` | 2 | Threads per worker (sync class) |
| `GUNICORN_WORKER_CLASS` | sync | Use `gevent`/`gthread` only after explicit benchmarking |
| `GUNICORN_TIMEOUT` | 30 | Worker is killed if a request exceeds this |
| `CELERY_CONCURRENCY` | 4 | Inner pool size of each Celery worker |

Sizing rule of thumb (for the demo):
`GUNICORN_WORKERS = (2 * num_cpu) + 1`. The accompanying report must
justify the chosen number on the actual demo hardware.

### Inner cap — `core.resources.bounded_executor`

For request handlers that fan out work (e.g. checkout that needs to call
the payment gateway *and* warm caches *and* update analytics), an
unbounded `ThreadPoolExecutor` will saturate the database connection
pool. The bounded executor caps fan-out at
`settings.INTERNAL_POOL_MAX_CONCURRENCY` (env var
`INTERNAL_POOL_MAX_CONCURRENCY`).

## Failing fast vs. queueing

`acquire_slot(resource, timeout=...)` returns `False` quickly under
overload instead of queueing forever. This is the difference between a
service that **degrades** (some requests fail with 503 they can retry)
and a service that **collapses** (every request piles up behind the
slow path until memory blows up).

Decision the NFR2 owner must defend in the demo:

> What is the maximum DB connections we are willing to consume per
> instance? `pg_max_connections >= GUNICORN_WORKERS * INSTANCES * CONN_MAX_AGE`
> must hold.

## Why these specific tools

- **Sync Gunicorn workers**: they make per-request resource consumption
  predictable and easy to count. Async workers (gevent/uvicorn) hide the
  cost behind cooperative scheduling and need separate tuning expertise.
- **Bounded ThreadPoolExecutor over `concurrent.futures` defaults**:
  `ThreadPoolExecutor(None)` defaults to `min(32, os.cpu_count() + 4)` —
  a number that has nothing to do with our DB connection budget.
- **Token-bucket admission control**: simple, easy to instrument; backed
  by an in-process `BoundedSemaphore` (or Redis if cross-instance
  fairness matters).

## Acceptance criteria

1. The Locust 100-VU mixed scenario (NFR9) finishes with **zero** worker
   crashes (`SIGKILL`) and **zero** `OperationalError: too many
   connections`.
2. The `get_pool_stats()` endpoint shows live in-flight counters that
   match the configured caps.
3. The NFR2 report shows two runs (with the same code, different
   `GUNICORN_WORKERS`) and demonstrates the effect on p95 + error rate.

## Files to ship

- `core/resources/pool.py` — `bounded_executor`, `acquire_slot`,
  `get_pool_stats`.
- A diagnostic endpoint (suggested: `GET /api/v1/_diag/pool/`).
- `docs/benchmarks/nfr2-tuning.md` with the numbers from the two runs.
