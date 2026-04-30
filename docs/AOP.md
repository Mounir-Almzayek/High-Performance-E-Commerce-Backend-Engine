# Aspect-Oriented Programming (AOP)

This document describes how cross-cutting concerns are kept out of business
code, where the instrumentation points live, and how to read the data they
produce.

---

## 1. Why AOP here?

Concerns like timing, auditing, and call counting are **orthogonal** to
business logic: they have to be applied to many code paths, and they have
to be removable without rewriting those paths. Inlining them produces
noisy services and makes it impossible to disable instrumentation in
production without a code change.

We solve this with three idiomatic Django mechanisms:

| Aspect | Mechanism | File |
|---|---|---|
| HTTP-level timing & instance tagging | Middleware | `core/aop/middleware.py` |
| Function-level timing / audit / counting | Decorators | `core/aop/decorators.py` |
| Persistence-level audit | Signals | `core/aop/signals.py` |

---

## 2. PerformanceMiddleware

Wraps every HTTP request:
1. Records `time.perf_counter()` before downstream middleware runs.
2. Lets the response build.
3. Computes elapsed time, adds `X-Instance-Id` and `X-Response-Time-ms`
   response headers, and emits a structured log line.

Why these headers matter:
- `X-Instance-Id` lets the NFR5 owner verify that Nginx is actually
  spreading traffic across web1 and web2 (not pinning everything to one).
- `X-Response-Time-ms` is the per-request datum that NFR10 aggregates
  into p50 / p95 / p99 reports.

The middleware is registered early in the `MIDDLEWARE` list so it covers
authentication, view, and session middleware in its window.

---

## 3. Decorators

```python
from core.aop.decorators import timed, audit_log, count_calls

@timed("orders.place_order")
@audit_log("orders.place_order")
@count_calls("orders.place_order")
def place_order(...):
    ...
```

| Decorator | Output | NFR consumer |
|---|---|---|
| `@timed(label)` | `INFO timed label=... ms=...` | NFR10 (latency) |
| `@audit_log(action)` | `INFO audit.start / audit.ok / audit.fail` | NFR1 incident triage |
| `@count_calls(label)` | in-process counter | NFR10 hot-path discovery |

Application convention: place the decorators on the **service** function
(not the view), so the timing window covers the entire business
transaction (including DB locks) and is reusable from Celery / CLI.

### Recommended decoration policy

- Every public function in `apps/<feature>/services.py` gets `@timed`.
- Every function that takes a row lock or distributed lock additionally
  gets `@audit_log`.
- Hot endpoints (catalog browse, place_order, capture_payment) get
  `@count_calls` for the NFR10 report.

---

## 4. Signals

`core/aop/signals.py` exposes a generic `log_save(sender, instance, ...)`
receiver. Each app wires it to the entities whose lifecycle is auditable:

```python
# apps/orders/apps.py
from django.apps import AppConfig
from django.db.models.signals import post_save

class OrdersConfig(AppConfig):
    ...
    def ready(self):
        from core.aop.signals import log_save
        from .models import Order
        post_save.connect(log_save, sender=Order)
```

This keeps the audit trail consistent across every persistence pathway,
including admin edits and Celery tasks, without polluting the model
classes.

---

## 5. django-silk

`silk` is wired in for development to get DB-query level profiles per
request:

- UI: `http://localhost/silk/`
- Useful when investigating an N+1 query suspect or comparing query plans
  before/after the NFR10 optimization.

In production this should be disabled (it adds overhead).

---

## 6. Toggling instrumentation

Production-time disablement is two steps:
1. Remove `PerformanceMiddleware` and `silk.middleware.SilkyMiddleware`
   from `MIDDLEWARE` in `config/settings/prod.py`.
2. Optional: skip `@count_calls` registration if it ever becomes a hot
   path itself (a `Counter.update()` is fast but not free).

That is the entire blast radius of the AOP layer — by design.
