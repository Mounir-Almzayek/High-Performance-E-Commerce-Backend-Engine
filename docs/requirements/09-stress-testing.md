# NFR9 — Stress testing

> Owner: _unassigned_ — stub-ready in `tests/stress/locustfile.py` and
> `docker-compose.yml` (locust service exposed on `:8089`).

## Objective

Prove that the system serves **at least 100 concurrent users** without
crashes or data loss. The artifact is a markdown report with quantitative
evidence, not a live demo claim.

## Scenarios

| Scenario | Mix | What it stresses |
|---|---|---|
| BrowseOnly | 100 % reads | Cache layer (NFR6), Nginx LB (NFR5) |
| CheckoutFlow | 100 % writes | Locks (NFR1, NFR7), transactions (NFR8) |
| WebhookStorm | duplicates | Idempotency on `external_id` (NFR1) |
| Mixed (KPI) | 80 % browse + 20 % checkout | The full system |

The Mixed scenario is the **headline number** for the report.

## Setup

`tests/stress/locustfile.py` declares `BrowseOnly`, `CheckoutFlow`, and
`WebhookStorm` user classes. Owner must:

1. Implement `on_start` to register/login a fresh user and store auth.
2. Pre-seed the DB with a realistic catalog (use `factory_boy` from
   `requirements-dev.txt`).
3. Sample product IDs from the seeded set rather than hard-coding `1`.
4. Run the test against `http://nginx` (already wired in compose) to
   exercise the full stack.

## Run procedure

```bash
docker-compose up --build
docker-compose exec web1 python manage.py loaddata fixtures/seed.json
# Open http://localhost:8089
# Set: 100 users, spawn rate 10/s, host=http://nginx, scenario=Mixed
# Run for 10 minutes
# Click "Download Data" -> CSV
```

## Required metrics in the report

- p50 / p95 / p99 latency per endpoint.
- Failure count and types.
- Throughput (RPS) achieved.
- DB / Redis / Celery saturation (CPU, connection count, queue depth).
- Distribution histogram across web1 / web2 (uses `X-Served-By`).

## Failure modes to look for

| Symptom | Likely cause | Cross-ref |
|---|---|---|
| `OperationalError: too many connections` | Pool starvation | NFR2 |
| `IntegrityError: unique violation` on `WebhookEvent.signature` | Idempotency working as designed (good!) | NFR1 |
| Oversold inventory | Locks not held / wrong order | NFR1 / NFR7 |
| p99 spike when one node is killed | Nginx not configured for failover | NFR5 |

## Acceptance criteria

1. Mixed scenario at 100 VU for 10 minutes finishes with **zero**
   `5xx` errors and **zero** integrity-audit violations.
2. Doubling VUs to 200 produces gracefully degraded latency, not a
   crash. (Submission required even if the report just shows where the
   knee in the latency curve is.)
3. Killing one Django instance mid-run produces ≤ 5 failures (the
   in-flight ones).

## Files to ship

- `tests/stress/locustfile.py` — full implementation.
- `fixtures/seed.json` (or a `tools/seed.py` management command).
- `docs/benchmarks/nfr9-stress-<date>.md` with charts (paste from
  Locust's CSV / image export).
