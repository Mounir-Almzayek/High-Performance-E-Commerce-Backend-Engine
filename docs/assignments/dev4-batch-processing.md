# Developer 4 — Batch processing (NFR4)

## Your scope

You own the **scheduled** background workload: walking large data sets
in chunks and aggregating them in parallel without exhausting memory or
starving foreground traffic.

The flagship deliverable is the daily sales aggregation, but the chunked
helpers you build belong to `core/batch/` and may be reused by future
batch jobs.

## Files you will write code in

| File | What you'll do |
|---|---|
| `core/batch/chunked.py` | Implement `iter_in_chunks`, `process_in_parallel`, `DailySalesAggregator` |
| `tasks/daily_sales_batch.py` | Implement `run_daily_sales` and `_aggregate_chunk` |
| `apps/orders/models.py` (extend) | Add `DailySalesReport` model storing the aggregate row |
| `apps/orders/admin.py` | Register `DailySalesReport` so the demo can show it |
| `tasks/__init__.py` | Confirm beat schedule (already wired at 02:00 UTC) |

## Files you will read but not modify

- `docs/requirements/04-batch-processing.md` — your spec.
- `core/resources/pool.py` — your parallel runner MUST use
  `bounded_executor` so NFR2 caps still apply.

## Definition of done

- The job completes on a 100k-row test data set with bounded RSS (does
  not scale with row count).
- Result of the chunked-parallel run equals the result of a one-pass
  serial aggregation on the same data (correctness proof).
- The NFR4 report shows runtime for at least four (chunk_size, workers)
  combinations and explains why the chosen pair is best.
- Running the batch concurrently with Locust's mixed scenario shows no
  catastrophic foreground regression (document the budget).

## Tips

- Use `qs.iterator(chunk_size=N)` — DO NOT call `.all()` on a large
  queryset.
- `DailySalesAggregator.merge` MUST be commutative + associative. Sums
  and counts are; medians are not.
- Run the job manually via:
  ```
  docker-compose exec celery_worker celery -A config call \
      tasks.daily_sales_batch.run_daily_sales
  ```
- Throw the result into the new `DailySalesReport` table inside a
  `transaction.atomic` block — partial writes are not acceptable.

## Demo prep

1. Seed 100k order items (factory-boy script).
2. Run the job from the Celery shell. Show:
   - Flower's chunk fan-out.
   - Memory profile (e.g. `psutil` polled every second) — flat line.
   - The persisted `DailySalesReport` row.
3. Show the comparison table from your report.
