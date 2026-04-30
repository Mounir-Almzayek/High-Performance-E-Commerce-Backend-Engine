# Architecture

## 1. Layered overview

The system is split into three clearly separated layers:

```
   +----------------------------------------------------------+
   |  Presentation (DRF Views + Serializers + URL Router)     |
   |  apps/<feature>/views.py, serializers.py, urls.py        |
   +----------------------------------------------------------+
                                |
   +----------------------------------------------------------+
   |  Domain Services (business logic)                        |
   |  apps/<feature>/services.py                              |
   |    - one function per use case (place_order, ...)        |
   |    - owns the concurrency points (locks, transactions)   |
   +----------------------------------------------------------+
                                |
   +----------------------------------------------------------+
   |  Cross-cutting concerns (AOP + shared infra)             |
   |  core/aop, core/concurrency, core/resources, core/cache, |
   |  core/batch, core/transactions, core/benchmarking        |
   +----------------------------------------------------------+
                                |
   +----------------------------------------------------------+
   |  Infrastructure                                          |
   |  Postgres   Redis   Celery   Nginx   Locust              |
   +----------------------------------------------------------+
```

### Why services.py is split out from views.py

- A view is responsible for HTTP only (input validation, JSON output).
- A service contains the business logic and the concurrency points.
- This split makes it possible to (a) test concurrency without spinning up
  the HTTP layer, and (b) reuse the same business call from a Celery task
  or a CLI management command.

---

## 2. Walk-through of the checkout flow

This single flow exercises most of the NFRs at once and is the canonical
example reviewers should consult during the demo.

```
Client --POST /orders/place--> Nginx
   |
   |  [NFR5] least_conn picks web1 or web2
   v
Gunicorn worker (Django) — [NFR2] capped by GUNICORN_WORKERS x THREADS
   |
   |  PerformanceMiddleware starts a timer — [AOP / NFR10]
   v
OrderViewSet.place
   |
   v
orders.services.place_order
   |
   |--> transaction.atomic — [NFR8] ACID
   |       |
   |       |--> Cart.objects.select_for_update — [NFR1 / NFR7] pessimistic lock
   |       |--> _calculate_totals
   |       |--> inventory.bulk_reserve (lock-ordered) — [NFR1]
   |       |--> Order + OrderItems INSERT
   |       |--> cart.status = checked_out
   |       |
   |       on_commit:
   |          - [NFR3] notifications.send_order_confirmation.delay
   |          - [NFR3] invoicing.generate_invoice.delay
   v
HTTP 201 + X-Instance-Id + X-Response-Time-ms — [AOP / NFR5 / NFR10]
```

### NFR coverage of this single flow

| Step | NFR |
|---|---|
| Nginx least_conn | NFR5 |
| Gunicorn worker cap | NFR2 |
| PerformanceMiddleware | AOP + NFR10 |
| Cart row lock | NFR1 + NFR7 |
| inventory.bulk_reserve | NFR1 |
| transaction.atomic | NFR8 |
| on_commit dispatch | NFR3 |

---

## 3. AOP at a glance

Full description in [AOP.md](AOP.md). Cross-cutting concerns live in
`core/aop/`:

- Decorators (`core/aop/decorators.py`):
  - `@timed("label")` measures wall-clock duration of any callable.
  - `@audit_log("action")` logs start / ok / fail of an invocation.
  - `@count_calls("label")` increments a hot-path counter for NFR10.
- Middleware (`core/aop/middleware.py`):
  - `PerformanceMiddleware` times every HTTP request and adds the
    `X-Instance-Id` and `X-Response-Time-ms` response headers.
- Signals (`core/aop/signals.py`): `post_save` audit hooks for the
  high-value entities.

Disabling instrumentation is a one-line change (remove a decorator or
unregister a middleware) — business code is never touched.

---

## 4. NFR-to-code map

Each requirement has a dedicated spec under [docs/requirements/](requirements/).
The table below pins each NFR to its primary implementation site.

| NFR | Primary file(s) | Owner |
|---|---|---|
| 1 — Concurrent access | `core/concurrency/locks.py`, `apps/inventory/services.py`, `apps/orders/services.py` | Dev 1 |
| 2 — Resource management | `core/resources/pool.py`, `docker/entrypoint.sh`, `.env` | Dev 2 |
| 3 — Async queues | `tasks/notifications.py`, `tasks/invoicing.py`, `config/celery.py` | Dev 3 |
| 4 — Batch processing | `core/batch/chunked.py`, `tasks/daily_sales_batch.py` | Dev 4 |
| 5 — Load distribution | `docker/nginx.conf`, `docker-compose.yml` | Dev 5 |
| 6 — Distributed cache | `core/cache/redis_cache.py`, callers in `apps/products/services.py` | unassigned |
| 7 — Locking strategies | `core/concurrency/locks.py` (optimistic helpers), `version` fields | unassigned |
| 8 — ACID | `core/transactions/atomic.py`, transaction boundaries in services | unassigned |
| 9 — Stress test | `tests/stress/locustfile.py` | unassigned |
| 10 — Benchmarking | `core/benchmarking/profiler.py` | unassigned |

---

## 5. Three concurrency tiers

The system uses three distinct concurrency-control mechanisms, each
solving a different problem. Reviewers should be able to point to the
exact file for each tier.

| Tier | Mechanism | What it protects |
|---|---|---|
| Process-level | Gunicorn `--workers` x `--threads`, Celery `--concurrency` | Caps total in-flight requests on each instance — protects RAM + DB pool. [NFR2] |
| In-process | `core.resources.bounded_executor`, semaphores | Caps fan-out work *inside* a single request. [NFR2] |
| Cross-process | Postgres row locks (`select_for_update`) and Redis `SET NX PX` locks | Coordinates writers running on web1 vs web2. [NFR1 / NFR7 / NFR8] |

Engineering note: a Python `threading.Lock` is **not** sufficient here.
web1 and web2 are different OS processes (typically on different hosts),
so the lock must live outside the process — Postgres or Redis.

---

## 6. Deployment topology

```
                    +----------------+
                    |     Nginx      |  (Load balancer — NFR5)
                    +-------+--------+
                            |
              +-------------+-------------+
              |                           |
        +-----v-----+               +-----v-----+
        |   web1    |               |   web2    |    Gunicorn x N workers
        | (Django)  |               | (Django)  |    NFR2
        +-----+-----+               +-----+-----+
              |                           |
              +------+--------------+-----+
                     |              |
             +-------v---+     +----v-------+
             | Postgres  |     |   Redis    |
             |  NFR8     |     | cache+broker|
             +-----------+     +------+-----+
                                      |
                            +---------+---------+
                            |                   |
                       +----v----+         +----v----+
                       | Celery  |         | Celery  |
                       | worker  |         |  beat   |
                       +---------+         +---------+
                          NFR3                NFR4
```
