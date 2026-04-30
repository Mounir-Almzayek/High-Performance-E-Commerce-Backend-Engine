# NFR4 — Batch processing

> Owner: **Dev 4**
> Status: stubs in `core/batch/chunked.py` and `tasks/daily_sales_batch.py`;
> beat schedule wired in `tasks/__init__.py` (02:00 UTC daily).

## Objective

Process the previous day's sales as a **scheduled background job** that
walks the data in chunks, aggregates per chunk in parallel, then merges.
This is the "batch processing" NFR — explicitly distinct from the async
queue NFR (NFR3). Async = unit-of-work per user action. Batch = scheduled
operation over a large data set.

## Job design

```
run_daily_sales (Celery beat 02:00 UTC)
     |
     |  window = [yesterday_00:00 UTC, today_00:00 UTC)
     |
     v
queryset = OrderItem.objects
              .filter(order__placed_at__range=window,
                      order__status__in=[paid, shipped, delivered])
              .iterator(chunk_size=1000)        # streaming - bounded memory
     |
     v
core.batch.process_in_parallel(
    queryset,
    handler=_aggregate_chunk,
    chunk_size=1000,
    max_workers=8,                              # via core.resources
)
     |
     v
merge per-chunk DailySalesAggregator -> DailySalesReport row
     |
     v
optional notification dispatch
```

## Why chunks

A naive `qs.all()` materializes every row in Python memory. A real-world
day of orders is millions of rows, so the worker OOMs. `iterator(chunk_size=N)`
streams from the DB cursor, and grouping into local lists keeps memory
bounded by `chunk_size`.

## Why parallel

Per-chunk aggregation is a pure function (no shared state during the
work itself), so it parallelizes trivially. With `max_workers=8` and
sane `chunk_size`, total runtime drops from O(N) to O(N / cores) until
the DB becomes the bottleneck.

The parallelization MUST go through `core.resources.bounded_executor` so
NFR2 caps still apply — a runaway batch job must not starve the
foreground HTTP traffic for connections.

## Why scheduled (not on-demand)

The batch is heavy and I/O-bound. Running it during peak hours degrades
NFR9 (foreground throughput). Beat at 02:00 UTC keeps it off the hot
window.

## Aggregator design

`DailySalesAggregator.merge(other)` MUST be **associative** and
**commutative** — otherwise reordering the chunks would change the
result. Both properties are trivially true for sums and counts; care is
needed if median / percentile metrics are added later.

## Acceptance criteria

1. Running the job over a seeded data set of 100k order items produces
   the same result as a synchronous one-pass aggregation (within
   floating-point tolerance for any monetary rounding).
2. Memory profile during the run stays bounded — peak RSS does not
   scale with dataset size.
3. Locust (NFR9) running concurrently with the batch job shows no
   foreground latency regression beyond the documented budget.
4. The Flower UI shows the chunk fan-out and the merge step.

## Files to ship

- `core/batch/chunked.py` — `iter_in_chunks`, `process_in_parallel`,
  `DailySalesAggregator`.
- `tasks/daily_sales_batch.py` — entry point, chunk handler, persistence.
- A model `DailySalesReport` (suggested: in `apps/orders/models.py`)
  storing the daily aggregate for later reporting.
- `docs/benchmarks/nfr4-batch.md` with timing for serial vs. parallel
  runs at multiple `chunk_size` and `max_workers` settings.
