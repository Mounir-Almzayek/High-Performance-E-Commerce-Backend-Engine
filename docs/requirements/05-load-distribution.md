# NFR5 — Load distribution

> Owner: **Dev 5**
> Status: Nginx + two web instances wired in `docker-compose.yml` and
> `docker/nginx.conf`. Strategy chosen and rationale documented.

## Objective

Simulate horizontal scaling: distribute incoming HTTP traffic across two
identical Django instances and demonstrate that:

- The system continues to serve traffic if one instance dies.
- The chosen distribution strategy is the right one for the workload, with
  evidence.

## Topology

```
Client -> Nginx (port 80) -> { web1:8000, web2:8000 }
```

Nginx upstream block:

```nginx
upstream django_backend {
    least_conn;
    server web1:8000 max_fails=3 fail_timeout=10s;
    server web2:8000 max_fails=3 fail_timeout=10s;
}
```

`X-Served-By` and `X-Instance-Id` are exposed on every response so the
distribution can be observed without enabling Nginx debug logs.

## Strategy: least_conn (chosen)

E-commerce traffic is **highly skewed**: a checkout is much heavier than
a category browse. Pure round-robin can pile multiple heavy requests on
the same backend and cause local saturation. `least_conn` forwards each
new request to whichever backend has the fewest in-flight connections —
the simplest strategy that adapts to workload skew.

## Rejected: round_robin

Suitable only when request cost is uniform. Documented as a baseline in
the NFR5 report so the comparison shows why we rejected it.

## Rejected: ip_hash

Provides sticky sessions by hashing client IP. We don't need stickiness
because:
- Sessions live in Redis (`SESSION_ENGINE = "...cache"`).
- Stickiness can hide imbalance during the demo and prevent observing
  load distribution at all.

## Statelessness contract

For LB to work without `ip_hash`, NO state can live in process memory:

- ✅ Sessions in Redis (settings/base.py).
- ✅ Cache in Redis.
- ✅ Celery broker + result in Redis.
- ❌ No global Python dicts, no in-process counters used as source of
  truth (the `core.aop.decorators._call_counter` is per-process and is
  a *diagnostic*, not a source of truth — it should move to Redis under
  NFR10 if needed for cross-instance views).

## Failure-mode demo

Steps the NFR5 owner must walk through during the demo:

1. Run Locust with 100 VUs against `http://localhost`.
2. `docker-compose stop web1`. Show that traffic continues via web2 and
   that p95 momentarily spikes then recovers.
3. `docker-compose start web1`. Show that Nginx re-includes it
   automatically.

## Acceptance criteria

1. Distribution histogram across `X-Served-By` is within 10 % of even on
   the Locust mixed scenario at 100 VU.
2. Killing one backend produces zero failed requests (modulo in-flight
   ones at the moment of kill).
3. The NFR5 report includes a histogram for both round-robin and
   least_conn against the same scenario, with discussion.

## Files to ship

- `docker/nginx.conf` (already drafted) with final tuning.
- A short script `tools/distribution_check.sh` that fires N curls and
  histograms `X-Served-By`.
- `docs/benchmarks/nfr5-balancing.md` with the histogram + failure
  recovery time.
