# Developer 10 — Benchmarking & bottleneck analysis (NFR10)

## Your scope

You own the **numerical before/after proof** of at least one bottleneck
fix. The examiner's whole emphasis lives here: not "we made it faster" but
*"here is the bottleneck, here is why it was slow, here is the fix, and
here are the numbers under the same load."* A number without a baseline
cannot be defended — so the freeze-measure-fix-measure sequence is the
deliverable, not the speedup itself.

You pair with Dev 9 (their Locust CSV is your raw data) and Dev 6 (the
cache is one of the best bottleneck-fix candidates to showcase).

## Files you will write code in

| File | What you'll do |
|---|---|
| `core/benchmarking/profiler.py` | Implement `capture_baseline(scenario)`, `capture_after(scenario)`, `top_n_hot_paths(n=10)`; stubs already exist |
| New file(s): `docs/benchmarks/<scenario>.md` | One report per bottleneck investigated, with the before/after table |
| New file: `tools/benchmark.sh` | One command: run the Locust scenario + collect the artifact files |

## Files you will read but not modify

- `docs/requirements/10-benchmarking.md` — your spec (required report
  sections + the before/after table format).
- `core/aop/middleware.py` — `PerformanceMiddleware` per-request timing.
- `core/aop/decorators.py` — `@count_calls` / `get_call_counts` for
  hot-path discovery.
- django-silk at `/silk/` — per-request DB query plan.
- `tests/stress/` — Dev 9's Locust CSV (your aggregate p50/p95/p99 source).

## Definition of done

- At least **one** bottleneck identified, fixed, and reported with the
  full table: p50, p95, p99, DB queries/req, RPS sustained — before vs.
  after under the **same** load.
- The report references **commit hashes** for the "before" and "after"
  states, so the fix is auditable.
- The fix does **not** regress an unrelated metric (e.g. caching the
  product page must not serve stale prices — confirm Dev 6's invalidation
  test passes).
- The report has an **honest caveats** section: what you did not measure,
  what could regress.

## Tips

- Use `top_n_hot_paths()` to **pick** the bottleneck from measured data —
  don't guess which path is slow.
- Strongest, easy-to-defend candidates:
  - N+1 query in catalog browse → `prefetch_related` (report the query
    count drop),
  - cache miss on hot product detail → add Dev 6's cache (report latency
    drop),
  - lock contention on `StockItem` → the Dev 7 mechanism switch (report
    throughput delta).
- The sequence is mandatory: freeze at commit A → run scenario → fix at
  commit B → run the *same* scenario → diff. Same load, same hardware.

## Demo prep

1. Show `top_n_hot_paths()` flagging the bottleneck (e.g. catalog browse
   dominates total time).
2. Show the "before" snapshot: silk query count + p95 at commit A.
3. Apply the fix (state the commit hash), show the "after" snapshot at
   commit B.
4. Present the before/after table and say the one sentence: e.g. *"The
   bottleneck wasn't order creation — it was the synchronous invoice +
   email. Moving them to a queue cut checkout p95 from 1800 ms to 350 ms
   without dropping the feature."*
