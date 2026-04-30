# NFR10 — Benchmarking and bottleneck analysis

> Owner: _unassigned_ — stub-ready in `core/benchmarking/profiler.py`.
> Already-collecting instrumentation: `PerformanceMiddleware`, `@timed`,
> `@count_calls`, django-silk.

## Objective

Identify at least **one** bottleneck in the running system and present a
**numerical before/after** comparison: same workload, same hardware, two
implementations.

## What counts as a "bottleneck" for this NFR

A code path whose latency or resource use dominates a representative
workload. Examples that the team can reasonably uncover and fix:

- N+1 query in catalog browse → fix with `prefetch_related` (and report
  query count drop).
- Cache miss on the hot product detail → add NFR6 cache (and report
  latency drop).
- Lock contention on `StockItem` under flash-sale → switch from
  pessimistic to optimistic for the right surface (and report
  throughput delta).

The chosen bottleneck must be defended on engineering grounds during the
review session: why this one, why now, what the rejected alternatives
would have given.

## Sources of evidence

| Source | Use | File |
|---|---|---|
| `PerformanceMiddleware` headers + log | per-request timing | `core/aop/middleware.py` |
| `@count_calls` snapshot | hot-path discovery | `core/aop/decorators.py::get_call_counts` |
| django-silk | per-request DB query plan | `/silk/` URL |
| Locust CSV | aggregate p50 / p95 / p99 | `tests/stress/` |

## API to implement

Stubs already exist in `core/benchmarking/profiler.py`:

- `capture_baseline(scenario_name)` — reads the live counters / silk
  data and persists a JSON snapshot under `benchmarks/<scenario>/before.json`.
- `capture_after(scenario_name)` — same, then writes a markdown report at
  `docs/benchmarks/<scenario>.md` containing a side-by-side diff.
- `top_n_hot_paths(n=10)` — returns `[(label, total_ms), ...]` sorted
  desc, used to *pick* the bottleneck.

## Required report sections

For each bottleneck the report MUST include:

1. **What** the bottleneck is (one sentence).
2. **How it was found** (the metric and threshold that flagged it).
3. **Why it exists** (root cause: missing index, missing cache, hot
   lock, ...).
4. **The fix** (commit reference + 2–3 sentences).
5. **Before / after numbers** under the **same** load. Suggested table:

   | Metric | Before | After | Delta |
   |---|---|---|---|
   | p50 (ms) | | | |
   | p95 (ms) | | | |
   | p99 (ms) | | | |
   | DB queries / req | | | |
   | RPS sustained | | | |

6. **Honest caveats** — what was *not* measured, what could regress.

## Why before/after, not "we made it fast"

Numbers without a baseline cannot be defended in review. The full
sequence is:

```
freeze code at commit A      <-- "before"
run Locust scenario X        <-- captured baseline
apply fix at commit B        <-- "after"
run Locust scenario X again  <-- captured after
diff and report
```

## Acceptance criteria

1. At least ONE bottleneck identified, fixed, and reported with the
   table above.
2. The report references commit hashes, so the fix is auditable.
3. The fix DOES NOT regress an unrelated metric (e.g. caching the
   product page must not cause stale prices — invalidation tested).

## Files to ship

- `core/benchmarking/profiler.py` — full implementation.
- `docs/benchmarks/<scenario>.md` for each bottleneck investigated.
- `tools/benchmark.sh` — one command that runs Locust + collects the
  artifact files.
