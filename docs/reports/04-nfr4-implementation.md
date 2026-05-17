# NFR4 - Batch Processing: Implementation Report

> Branch: `main` / NFR4 implementation
> Status: implemented, ready for performance benchmarking.

This report explains the batch-processing implementation, why chunked
parallel aggregation was chosen, and why it is the best design for a
daily sales job over large order data.

---

## 1. Scope of work

NFR4 implements a scheduled daily sales aggregation job.

| File | What changed |
|---|---|
| `core/batch/chunked.py` | Implemented chunked iteration, bounded parallel processing, and `DailySalesAggregator` |
| `apps/tasks/daily_sales_batch.py` | Implemented daily sales task, date windowing, chunk aggregation, merge, and persistence |
| `apps/orders/models.py` | Added `DailySalesReport` model |
| `apps/orders/admin.py` | Registered `DailySalesReport` for admin/demo visibility |
| `apps/orders/management/commands/run_daily_sales.py` | Added a manual command path for running and tuning the job |
| `apps/orders/migrations/0001_initial.py` | Persists `DailySalesReport` schema |

---

## 2. The problem

Daily sales reporting is not a request-time operation. It scans many
`OrderItem` rows, aggregates totals, and stores a report row. If this
work is done naively with `qs.all()`, memory usage grows with the number
of rows and the worker can crash on large data sets.

NFR4 needs the opposite behavior:

- memory bounded by chunk size
- parallelism bounded by NFR2 resource caps
- deterministic final totals
- one persisted report per day

---

## 3. Chosen solution

The implemented flow is:

1. Determine yesterday's UTC window.
2. Query completed order items only.
3. Stream the queryset with `iterator(chunk_size=...)`.
4. Group rows into fixed-size chunks.
5. Process chunks in parallel through `bounded_executor(resource="batch")`.
6. Merge partial results.
7. Persist one `DailySalesReport` row with `update_or_create`.

The core pieces are:

| Component | Purpose |
|---|---|
| `iter_in_chunks(...)` | Streams rows without materializing the full queryset |
| `process_in_parallel(...)` | Fans out chunk handlers through the bounded executor |
| `_aggregate_chunk(...)` | Builds one partial aggregate from one chunk |
| `DailySalesAggregator.feed(...)` | Accumulates counts, revenue, item totals, and product breakdown |
| `DailySalesAggregator.merge(...)` | Combines partial aggregates |
| `DailySalesReport` | Stores the final daily result |

---

## 4. Why this was the best choice

This solution was best because it makes the job scalable without letting
the batch workload harm foreground traffic.

### 4.1 Chunking bounds memory

The most important decision is using `queryset.iterator(chunk_size=N)`
plus local chunk lists. Memory usage is tied to `chunk_size`, not total
row count. That makes a 100k-row day and a million-row day follow the
same memory pattern, only with more chunks.

### 4.2 Parallel chunk handling improves runtime safely

Each chunk aggregation is independent. Counting orders, summing revenue,
summing quantities, and building per-product totals do not require
shared mutable state during chunk processing. That makes chunk-level
parallelism a natural fit.

The parallelism still goes through NFR2's `bounded_executor`, so the
batch job cannot create unlimited threads or consume all database-facing
capacity.

### 4.3 Scheduled execution protects the hot path

The job is designed as scheduled background work, not an on-demand API
request. Running it at the planned off-peak window keeps heavy reporting
away from checkout and payment traffic.

### 4.4 The merge operation is deterministic

`DailySalesAggregator.merge(...)` combines sums and product totals.
Those operations are associative and commutative, so chunk completion
order does not affect the final result. This matters because
`as_completed(...)` returns chunks in completion order, not submit order.

### 4.5 Persistence is idempotent

The report is saved with `update_or_create(date=...)`. Re-running the
job for the same day updates the same row instead of creating duplicate
reports.

---

## 5. Important implementation decisions

### 5.1 Completed orders only

The query includes `PAID`, `SHIPPED`, and `DELIVERED` orders. Pending or
cancelled orders are excluded so the report reflects real sales, not
attempted purchases.

### 5.2 One report row per date

`DailySalesReport.date` is unique. This gives the batch result a stable
identity and makes reruns safe.

### 5.3 Manual command support

`run_daily_sales` gives the team a practical demo and tuning path. It
allows different chunk sizes and worker counts to be tested without
changing application code.

### 5.4 Bounded executor for worker control

The batch job uses the `batch` resource pool. This is better than a raw
`ThreadPoolExecutor` because raw executors can accidentally exceed the
capacity budget set by NFR2.

---

## 6. Tuning guidance

The tuning knobs are:

| Knob | Effect |
|---|---|
| `chunk_size` | Larger chunks reduce scheduling overhead but use more memory |
| `max_workers` | More workers improve throughput until DB/CPU contention dominates |
| `RESOURCE_BATCH_MAX_CONCURRENCY` | Hard cap that protects the rest of the service |

Recommended starting point for the demo:

| Setting | Value | Why |
|---|---:|---|
| `chunk_size` | 1000 | Good balance between memory and overhead |
| `max_workers` | 4 to 8 | Enough parallelism without overwhelming DB connections |
| `RESOURCE_BATCH_MAX_CONCURRENCY` | 4 | Keeps batch below checkout/payment priority |

The report should be completed with measured timings for several
`(chunk_size, max_workers)` pairs after the dataset is seeded.

---

## 7. Demo explanation

The clean demo story is:

1. Seed a large set of completed order items.
2. Run the batch job manually.
3. Show that the job processes fixed-size chunks.
4. Show the final `DailySalesReport` row.
5. Compare serial vs. parallel timing.
6. Explain that the chosen configuration wins because it reduces runtime
   while keeping memory and batch concurrency bounded.

---

## 8. Review note

The merge path should be verified with a seeded dataset before the final
demo, especially for `total_orders`, because preserving unique order IDs
across serialized chunk results is stricter than summing item-level
totals.

---

## 9. Summary

The batch solution is best because it separates heavy reporting from
request traffic, streams large data safely, parallelizes only independent
work, and writes one idempotent report row. It gives the project a
scalable batch pattern instead of a one-off script that works only on
small data.
