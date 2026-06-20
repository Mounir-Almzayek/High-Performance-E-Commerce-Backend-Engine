# NFR7 locking benchmark

## Decision

Production inventory reservation uses pessimistic row locking:
`SELECT ... FOR UPDATE` inside `transaction.atomic()`.

Inventory is a high-contention correctness boundary. During a flash sale,
many requests compete for a small number of rows. Pessimistic locking
queues those writers at PostgreSQL and lets each request make its
availability decision against current state. An optimistic implementation
would repeatedly read stale versions, lose CAS attempts, and retry under
the exact workload where inventory matters most.

Product price and metadata updates remain optimistic. Those writes are
rare and usually initiated by a human administrator, so avoiding a held
row lock is useful and conflicts are uncommon. A stale Product edit is
returned as HTTP 409 for human review; it is not automatically retried.

Loyalty points use neither inventory-style locking nor Product-style CAS.
They are a pure counter, so an atomic SQL F-expression update performs the
arithmetic and version bump in one statement. Conditional deductions also
prevent the balance from going negative.

## Benchmark tool

Run from the repository root against a Product that already has a
`StockItem`:

```bash
python tools/benchmarks/nfr7_locking_benchmark.py \
  --product-id 1 \
  --stock 100 \
  --workers 50 \
  --requests 200 \
  --mode all
```

Arguments:

| Argument | Meaning |
|---|---|
| `--product-id` | Existing Product whose `StockItem` is benchmarked |
| `--stock` | Initial `on_hand` value before each mode |
| `--workers` | Thread-pool concurrency |
| `--requests` | Total one-unit reservation attempts per mode |
| `--mode` | `all`, `nolock`, `pessimistic`, or `optimistic` |

The tool resets the selected stock row and deletes its movements before
each mode. Do not run it against production data. Results are written to
`results/nfr7_locking_results.json`.

## Compared modes

| Mode | Implementation | Production use |
|---|---|---|
| No-lock | Deliberately unsafe Python read-modify-write baseline | Never |
| Pessimistic | Production `apps.inventory.services.reserve_stock` | Yes |
| Optimistic | Benchmark-only StockItem version CAS with conflict retries | No |

`oversell` is calculated from accepted reservations beyond initial stock,
not only from the final `reserved` value. This exposes lost updates in the
unsafe baseline, where many requests may report success even though later
writes overwrite earlier reservations.

## Result table template

Populate this table from `results/nfr7_locking_results.json` after running
the benchmark in the target environment.

| Mode | Successes | Rejections | Oversell | Duration (s) | Throughput (ops/s) | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Retries | Deadlocks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| No-lock unsafe baseline | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | 0 | TBD |
| Pessimistic production | TBD | TBD | 0 expected | TBD | TBD | TBD | TBD | TBD | TBD | 0 | TBD |
| Optimistic benchmark-only CAS | TBD | TBD | 0 expected | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## Interpretation

Correctness is the first gate: any mode with `oversell > 0` is
disqualified regardless of throughput. Among correct modes, compare tail
latency, retry count, and throughput under increasing contention.

The expected pattern is:

- The no-lock baseline can appear fast but reports false successes and
  oversells.
- Pessimistic locking remains correct with no application retries and
  predictable behavior on the hot row.
- Optimistic CAS remains correct but accumulates retries as contention
  rises, making it a poor fit for inventory.

These results justify optimistic CAS for low-contention Product metadata,
not for hot inventory rows.
