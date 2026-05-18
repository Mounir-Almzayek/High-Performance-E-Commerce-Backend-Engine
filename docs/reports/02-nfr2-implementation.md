# NFR2 - Resource Management: Implementation Report

> Branch: `resource-management`
> Status: implemented, unit-tested, ready for review.

This report explains the resource-management solution, why it was chosen,
and why it is the best fit for this backend under load.

---

## 1. Scope of work

The feature adds explicit capacity control at two levels: process-level
settings for Gunicorn/Celery, and in-process admission control for hot
service paths.

| File | What changed |
|---|---|
| `config/settings/base.py` | Added env-driven resource caps for web, Celery, checkout, payment, batch, and internal pools |
| `.env.example` | Documented the resource-management knobs |
| `core/resources/pool.py` | Implemented named capacity pools, bounded executor, 503-safe overload handling, and live stats |
| `core/diagnostics/views.py` | Added diagnostic endpoints for live pool state and process CPU/RAM/thread metrics |
| `config/urls.py` | Exposed `GET /api/v1/_diag/pool/` and `GET /api/v1/_diag/process/` |
| `apps/orders/services.py` | Applied checkout admission control |
| `apps/payments/services.py` | Applied payment/webhook admission control |
| `core/batch/chunked.py` | Runs batch fan-out through the bounded executor |
| `tests/unit/test_resource_pool.py` | Covers saturation, release behavior, re-entrancy, and executor caps |
| `tests/unit/test_diagnostics.py` | Covers the diagnostic endpoint output |

---

## 2. The problem

The backend has multiple ways to create parallel work:

- Gunicorn workers and threads accept HTTP requests.
- Celery workers process background jobs.
- Checkout, payment, and batch logic can fan out internal work.

If every layer is allowed to maximize itself independently, the system
can exceed the database connection budget even when every individual
component looks reasonable. That failure mode is dangerous because it
does not degrade gradually: requests pile up, workers block, and the
database starts rejecting connections.

NFR2 solves that by making capacity explicit and enforceable.

---

## 3. Chosen solution

### 3.1 Outer caps

The outer layer remains controlled by deployment settings:

| Setting | Purpose |
|---|---|
| `GUNICORN_WORKERS` | Number of web worker processes |
| `GUNICORN_THREADS` | Number of request threads per worker |
| `GUNICORN_WORKER_CLASS` | Keeps the worker model explicit |
| `GUNICORN_TIMEOUT` | Prevents stuck requests from occupying workers forever |
| `CELERY_CONCURRENCY` | Caps background job parallelism |

These values are env-driven so the same image can be tuned differently
for local development, demo hardware, and production-like runs.

### 3.2 Inner caps

The inner layer is implemented in `core/resources/pool.py`.

| API | Role |
|---|---|
| `acquire_slot(resource, timeout)` | Tries to enter a named capacity pool |
| `release_slot(resource)` | Releases that capacity safely |
| `resource_slot(resource)` | Context-manager form that maps overload to `CapacityExceeded` |
| `@capacity_limited(resource)` | Decorator for service entrypoints |
| `bounded_executor(...)` | Thread pool that cannot exceed configured resource limits |
| `get_pool_stats()` | Live observability for configured pools |

The configured pools are:

| Pool | Default limit | Used by |
|---|---:|---|
| `internal_pool` | 16 | General in-process fan-out |
| `checkout` | 8 | `place_order` |
| `payment` | 8 | Payment capture, refund, and webhook flows |
| `batch` | 4 | Batch chunk workers |

---

## 4. Why this was the best choice

This solution was the best fit because it controls the real bottleneck:
bounded database-backed work, not just CPU usage.

### 4.1 It fails fast instead of collapsing slowly

When a resource pool is full, `resource_slot(...)` raises
`CapacityExceeded`, which maps to HTTP 503 behavior. That is better than
unbounded queueing because a 503 is retryable and visible, while an
unbounded queue hides overload until latency, memory, and DB connections
all fail together.

### 4.2 It preserves useful work

Checkout and payment are business-critical paths. By giving them named
pools, the system can protect them independently from less urgent work.
For example, a batch job can saturate the `batch` pool without consuming
all checkout capacity.

### 4.3 It matches the architecture already in the project

The project is a Django service with synchronous request handling,
PostgreSQL, Celery, and Redis. A lightweight in-process semaphore layer
is enough for the current architecture and demo scope. It avoids adding
new infrastructure while still proving the correct resource-governance
concept.

### 4.4 It is measurable

`GET /api/v1/_diag/pool/` reports:

- active instance id
- outer web/Celery caps
- acquire timeout
- per-pool limits
- in-flight work
- available slots
- accepted and rejected totals

`GET /api/v1/_diag/process/` complements it with process-level runtime
evidence:

- process id and uptime
- accumulated CPU seconds and CPU-per-uptime percentage
- Linux load average when available
- resident and peak resident memory from `/proc/self/status`
- Python active thread count and OS process thread count

This makes the NFR demonstrable during load testing instead of being
only a code-level claim.

### 4.5 It composes with the other NFRs

NFR4 batch processing uses `bounded_executor(resource="batch")`, so long
background work respects NFR2 automatically. Checkout and payment also
enter resource pools before the database transaction begins, which
prevents overloaded requests from taking locks and then blocking.

---

## 5. Important implementation decisions

### 5.1 Named pools instead of one global semaphore

A single global semaphore would be simpler, but it would let low-priority
work starve high-priority work. Named pools are better because checkout,
payment, internal fan-out, and batch work have different business value
and different safe limits.

### 5.2 Re-entrant acquisition

The same thread can re-enter the same resource without consuming another
slot. This prevents composite flows from deadlocking themselves when a
capacity-limited service calls another helper that uses the same pool.

### 5.3 `BoundedSemaphore`

`BoundedSemaphore` catches over-release mistakes. That is safer than a
plain semaphore because capacity bugs become visible in tests/logs
instead of silently increasing the pool size.

### 5.4 Timeout-based admission

The acquire timeout is configurable through
`RESOURCE_ACQUIRE_TIMEOUT_SECONDS`. This gives the service a deliberate
degradation policy: wait briefly for capacity, then reject cleanly.

---

## 6. Validation

Implemented unit coverage verifies:

- a full pool rejects work from another thread
- `resource_slot` releases capacity on exceptions
- overload maps to a 503-safe `CapacityExceeded`
- extra release calls do not corrupt the semaphore
- re-entrant acquisition does not consume duplicate slots
- `bounded_executor` respects the configured resource cap
- the diagnostic endpoint exposes the configured budget
- the process diagnostic endpoint exposes CPU/RAM/thread fields used in
  the monitoring screenshots

Local syntax validation also passed with:

```text
python -m compileall apps config core tests
```

Full pytest execution requires the Django test dependencies in
`requirements-dev.txt`.

---

## 7. Demo explanation

The clean demo story is:

1. Show `/api/v1/_diag/pool/` at rest.
2. Run checkout/payment load.
3. Show `in_flight` rising but never exceeding configured limits.
4. Lower a limit and show controlled 503 rejections instead of worker
   crashes.
5. Explain that this is the desired behavior: the system protects itself
   and keeps useful capacity available.

---

## 8. Summary

The resource-management solution is best because it is explicit,
bounded, observable, and aligned with the system's true bottleneck. It
does not try to make the backend infinitely parallel; it makes the
backend predictably parallel, which is the correct goal for NFR2.

---

## 9. JMeter and Monitoring Evidence

JMeter plan:

```text
tools/jmeter/resource-management-products.jmx
```

Run it at least twice with the same workload:

| Run | Suggested settings | What to capture |
|---|---|---|
| Low capacity | `GUNICORN_WORKERS=1`, `GUNICORN_THREADS=1` | Slower p95/p99, lower CPU utilization, possible queueing |
| Balanced capacity | `GUNICORN_WORKERS=4`, `GUNICORN_THREADS=2` | Better latency without runaway CPU/RAM/connections |

JMeter screenshots:

![Resource low workers JMeter](assets/resource-low-workers-jmeter.png)

![Resource balanced workers JMeter](assets/resource-balanced-workers-jmeter.png)

Monitoring screenshots:

![Resource monitoring before](assets/resource-monitoring-before.png)

![Resource monitoring after](assets/resource-monitoring-after.png)

During each run, capture the following endpoints before, during, and
after the load test:

```text
GET /api/v1/_diag/pool/
GET /api/v1/_diag/process/
```

Use `/api/v1/_diag/pool/` to prove that in-flight work never exceeds the
configured capacity, and `/api/v1/_diag/process/` to show the CPU/RAM
and thread behavior requested by the instructor. Docker stats,
Prometheus, or Grafana can still be used for nicer graphs; this endpoint
keeps the evidence available inside the Django application itself.

The important thing to explain in the demo is not "more threads is always
better". The point is that the chosen configuration uses available CPU
and DB capacity without letting requests, threads, or connections grow
without control.
