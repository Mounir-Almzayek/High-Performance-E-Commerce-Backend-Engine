# Developer 5 — Load distribution (NFR5)

## Your scope

You own horizontal scaling: prove the system runs with two identical
backends behind Nginx, that traffic is distributed, and that one
backend dying does not take the system down.

This is the most "infrastructure" of the five assignments — most of your
work is in Nginx config, docker-compose, and a measurement script. You
will *not* write much Python, but you WILL be the person who explains to
the examiner why the cache must live in Redis (because of you).

## Files you will write code in

| File | What you'll do |
|---|---|
| `docker/nginx.conf` | Final tuning of timeouts, buffer sizes, max_fails (drafted) |
| `docker-compose.yml` | Confirm web1/web2 are symmetric; consider adding a third |
| New file: `tools/distribution_check.sh` | Fires N curls and histograms `X-Served-By` |
| New file: `tools/failover_demo.sh` | Stops one backend, runs Locust short burst, restarts |
| `apps/users/views.py` (or new diag view) | Tiny endpoint that prints `INSTANCE_ID` for sanity checks |

## Files you will read but not modify

- `docs/requirements/05-load-distribution.md` — your spec.
- `core/aop/middleware.py` — the `X-Instance-Id` header already exists,
  use it for the histogram.
- `config/settings/base.py` — confirm sessions and cache live in Redis
  (else stickiness is forced on us).

## Definition of done

- Running `tools/distribution_check.sh` with 1000 hits shows web1 and
  web2 each within 10 % of even.
- Stopping one backend mid-Locust produces ≤ 5 failed requests.
- The NFR5 report contains:
  - histograms for round-robin AND least_conn on the same scenario,
  - failover recovery time chart,
  - a paragraph defending the chosen strategy.

## Tips

- The decision tree in your spec is the headline of your report:
  > round_robin (rejected — uniform-cost assumption is wrong here)
  > ip_hash (rejected — sessions are stateless, stickiness wastes
  >          balance)
  > least_conn (chosen — adapts to skewed cost)
- Verify there are no in-process state leaks that would break LB:
  - search the codebase for `__init__.py` files that maintain state,
  - confirm `core.aop.decorators._call_counter` is documented as
    diagnostic-only (NFR5 owner has the right to ask it be moved to
    Redis).

## Demo prep

1. Open Locust at 100 VU. Curl `/healthz` 50 times in a tight loop and
   show the histogram of `X-Served-By`.
2. `docker-compose stop web1`. Show that p95 spikes briefly then
   recovers; no requests are stuck.
3. `docker-compose start web1`. Show that within `fail_timeout=10s`
   Nginx is sending traffic to it again.
