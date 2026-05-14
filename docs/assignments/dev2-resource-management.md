# Developer 2 — Resource management (NFR2)

## Your scope

You own the answer to: "How much parallelism can this system safely
handle, and how do we cap it on each layer?"

That's both the OUTER caps (Gunicorn / Celery worker counts) and the
INNER caps (bounded executor + admission control inside a single
request).

## Files you will write code in

| File | What you'll do |
|---|---|
| `core/resources/pool.py` | Implement `bounded_executor`, `acquire_slot`, `get_pool_stats` |
| `docker/entrypoint.sh` | Tune defaults; keep them env-driven |
| `.env.example` | Confirm caps are documented |
| New file: a diagnostic view (suggested `apps/users/views.py` or a `core/diagnostics/views.py`) | Expose `GET /api/v1/_diag/pool/` returning `get_pool_stats()` |
| `config/urls.py` | Wire the diagnostic view |

## Files you will read but not modify

- `docs/requirements/02-resource-management.md` — your spec.
- `docs/ARCHITECTURE.md` § 5 (three concurrency tiers) — explains where
  your work sits in the bigger picture.

## Definition of done

- `bounded_executor()` returns a thread pool capped by
  `INTERNAL_POOL_MAX_CONCURRENCY`.
- `acquire_slot(resource, timeout)` returns False quickly under
  saturation (no unbounded queueing).
- `resource_slot(...)` / `@capacity_limited(...)` map overload to HTTP
  503 before checkout or payment work enters a DB transaction.
- The diagnostic endpoint shows live counters per resource.
- The NFR2 report compares two `GUNICORN_WORKERS` settings on the same
  load and demonstrates the trade-off.
- A health rule is documented: web upper bound =
  `INSTANCES * GUNICORN_WORKERS * GUNICORN_THREADS`; Celery upper bound =
  `CELERY_WORKER_REPLICAS * CELERY_CONCURRENCY`; internal executor
  threads fit inside the remaining database budget.

## Tips

- For the inner pool, `concurrent.futures.ThreadPoolExecutor(max_workers=N)`
  is fine; just add a `thread_name_prefix` for debugging.
- For `acquire_slot`, `BoundedSemaphore` is enough for the project; a
  Redis variant only matters if cross-instance fairness is needed.
- Watch out: every Postgres connection is RAM. Don't blindly maximize
  workers.

## Demo prep

1. Open Locust at 100 VU. Show the diagnostic endpoint with live
   in-flight counts.
2. Drop `GUNICORN_WORKERS` to 1; show p95 ballooning.
3. Restore; show p95 back to baseline. Reference the table in your
   report.
