# Developer 8 — ACID transactions (NFR8)

## Your scope

You own atomicity of **composite operations**: placing an order touches
the order, the stock, the stock movement, the cart, and the payment
intent. Either all of them commit or none of them do. A half-finished
order — stock deducted but no payment, or payment taken but no order — is
the failure this NFR exists to prevent.

The other half of your job is knowing what to **keep out** of the
transaction. Sending the invoice email or rendering the PDF must NOT be
inside the commit — they go to the queue via `on_commit`. This is the
direct link to NFR3 and the strongest line in your review: *"critical
writes are atomic, secondary effects are deferred, so the user isn't kept
waiting and nothing is lost."*

## Files you will write code in

| File | What you'll do |
|---|---|
| `core/transactions/atomic.py` | Implement `atomic_with_isolation(level=...)`, `on_commit(callback)` (with structured logging), `run_saga(steps)` |
| `apps/orders/services.py` | `place_order`, `cancel_order` — every critical write inside one `atomic_with_isolation` block |
| `apps/payments/services.py` | `capture_payment`, `refund_payment` — same |
| `tests/unit/test_atomic_*.py` | Failure-injection: mock a mid-flow step to raise, assert ZERO partial state |
| New file: `tools/integrity_audit.sql` | Query that asserts inventory and orders are consistent after a run (used by Dev 9) |

## Files you will read but not modify

- `docs/requirements/08-acid-transactions.md` — your spec (the atomicity
  invariant list per operation is your contract).
- `core/concurrency/locks.py` — locks acquired by Dev 1 / Dev 7 live
  inside your transaction blocks; coordinate the lock order.
- `apps/*/tasks.py` / Celery dispatch — must fire on `on_commit`, never
  inline.
- `core/cache/redis_cache.py` — Dev 6's `invalidate_product` is also an
  `on_commit` callback; you provide the hook.

## Definition of done

- Each composite operation has a **failure-injection test** that asserts
  no partial state remains (no Order, no StockMovement, stock unchanged).
- The report lists, per operation, every write involved and confirms they
  are inside one `atomic_with_isolation` block (see the spec's
  `place_order` example).
- The isolation level for each operation is documented **with its reason**
  (default `READ COMMITTED`; escalate only with justification).
- `tools/integrity_audit.sql` returns zero inconsistencies after a Locust
  soak run (Dev 9 runs it at end-of-test).

## Tips

- **`on_commit` for Celery dispatch and cache invalidation.** If you
  dispatch the "order confirmed" email *inside* the transaction and it
  then rolls back, you've emailed the customer about an order that does
  not exist. Defer it.
- `SERIALIZABLE` is only safe with an explicit retry loop — Postgres
  returns `serialization_failure` and expects the app to retry.
- For the payment-gateway flow, 2PC is not available — use `run_saga`, and
  make every compensation **idempotent** (running it twice == once).

## Demo prep

1. Normal `place_order` → show Order + OrderItems + StockMovement +
   PaymentIntent all created together.
2. Inject a failure mid-flow (monkeypatch the payment-intent step to
   raise). Show ROLLBACK: no Order, stock unchanged, no payment row.
3. Show the confirmation email / invoice fires **only after** commit
   (via `on_commit`) and never fires on the rolled-back path.
