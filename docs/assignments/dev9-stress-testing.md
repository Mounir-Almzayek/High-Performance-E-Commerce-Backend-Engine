# Developer 9 — Stress testing (NFR9)

## Your scope

You own the proof that the system serves **at least 100 concurrent users**
without crashes or data loss. Your deliverable is a markdown report with
quantitative evidence — not a "it felt fast" claim. `locustfile.py` and
the compose `locust` service are stubbed and waiting.

Your scenarios exercise everyone else's work at once: the cache (Dev 6),
the load balancer (Dev 5), the locks (Dev 1 / Dev 7), and the transactions
(Dev 8). The single most convincing artifact you produce is the
**inventory-exhaustion test**: 100 users racing for 10 units, ending with
exactly 10 orders and zero negative stock. That one chart proves
concurrency correctness better than any paragraph.

## Files you will write code in

| File | What you'll do |
|---|---|
| `tests/stress/locustfile.py` | Implement `BrowseOnly`, `CheckoutFlow`, `WebhookStorm`, `Mixed`; `on_start` logs in a fresh user; sample real product IDs from the seeded set (don't hard-code `1`) |
| New: `apps/*/management/commands/seed_demo.py` (or `fixtures/seed.json`) | A realistic catalog to test against (use `factory_boy` from `requirements-dev.txt`) |
| `docker-compose.yml` | Confirm the `locust` service on `:8089`, `host=http://nginx` |
| New file: `docs/benchmarks/nfr9-stress-<date>.md` | The headline report (feeds Dev 10) |

## Files you will read but not modify

- `docs/requirements/09-stress-testing.md` — your spec (scenarios, metrics,
  acceptance criteria).
- `core/aop/middleware.py` — the `X-Served-By` / `X-Instance-Id` header you
  use for the distribution histogram.
- `tools/integrity_audit.sql` — Dev 8's query; run it at end-of-run to
  prove no data loss.
- `docs/assignments/dev10-benchmarking.md` — strong overlap; hand Dev 10
  your raw CSV so the before/after numbers come from the same load.

## Definition of done

- **Mixed scenario, 100 VU, 10 min** → zero `5xx` errors and zero
  integrity-audit violations.
- 200 VU → graceful degradation (latency knee documented), **not** a
  crash.
- Killing one Django instance mid-run → ≤ 5 failures (the in-flight ones).
- The report contains: p50 / p95 / p99 per endpoint, failure count and
  types, sustained RPS, DB / Redis / Celery saturation, and the
  `X-Served-By` distribution histogram.

## Tips

- Run against `http://nginx`, **not** a single `web` instance — otherwise
  you are not testing the load balancer (Dev 5) at all.
- `IntegrityError` on `WebhookEvent.signature` during `WebhookStorm` is
  idempotency **working as designed** — report it as a pass, not a bug.
- Watch for `OperationalError: too many connections` (pool starvation →
  NFR2) and oversold inventory (locks not held → NFR1 / NFR7).
- Capture the evidence the audit asks for: Locust CSV/HTML, Flower
  screenshots, `docker stats`, and `/api/v1/_diag/pool/` snapshots.

## Demo prep

1. Open Locust at 100 VU `Mixed`, run, show stable RPS and p95, error rate
   ≈ 0.
2. Run `tools/integrity_audit.sql` → 0 violations, no negative stock.
3. Inventory exhaustion: stock = 10, 100 buyers → exactly 10 succeed, 90
   clean failures, final stock = 0.
4. `docker-compose stop web1` mid-run → show p95 blips then recovers,
   ≤ 5 failures.
