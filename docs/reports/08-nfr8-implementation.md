# NFR8 — ACID Transactions: Implementation Report

> Branch: `feat/nfr8-acid-transactions` (merged into `master`)
> Owner: Developer 8
> Status: implemented — explicit-isolation transaction primitives, logged
> deferred side effects, a tested saga runner, failure-injection tests for the
> composite operations, and a post-run integrity audit.

This document explains every transaction-integrity decision taken for NFR8,
why it was taken, and how it maps to the lecture material on ACID and
concurrency.

---

## 1. Scope of work

| File | What was added / changed |
|---|---|
| `core/transactions/atomic.py` | Implemented the three primitives: `atomic_with_isolation`, `on_commit` (logged), `run_saga` (+`SagaStep`) |
| `apps/orders/services.py` | `place_order`, `cancel_order` now open `atomic_with_isolation("read committed")`; deferred dispatch goes through the logged `on_commit` |
| `apps/payments/services.py` | `capture_payment`, `refund_payment` now open `atomic_with_isolation("read committed")` |
| `tools/integrity_audit.sql` | Six-check post-run consistency audit (returns rows only on violation) |
| `tests/unit/test_atomic_saga.py` | Saga runner: forward order + reverse compensation on failure |
| `tests/unit/test_atomic_isolation.py` | `atomic_with_isolation` level-setting/validation + `on_commit` defer/drop |
| `tests/unit/test_atomic_place_order.py` | Failure injection → `place_order` leaves zero partial state |
| `tests/unit/test_atomic_capture_payment.py` | Failure injection after a real write → `capture_payment` rolls back everything |
| `docs/reports/08-nfr8-implementation.md` | This report |

No public HTTP contract changed. The refactor swapped the transaction
*mechanism* under four service functions without altering their behaviour —
proven by the failure-injection tests passing before and after the change.

---

## 2. The key finding (what NFR8 actually had to do)

The composite operations were **already atomic** before NFR8 started. NFR1
wrapped `place_order`, `capture_payment`, `refund_payment`, and `cancel_order`
in `@transaction.atomic` and serialized every contended row with
`SELECT ... FOR UPDATE`. So "make it all-or-nothing" was already done.

What `core/transactions/atomic.py` was missing — and what NFR8 delivers — is:

1. **Explicit, reviewable isolation.** `@transaction.atomic` hides the
   isolation level. `atomic_with_isolation("read committed")` puts the choice
   in the source and in the SQL log, where a reviewer can challenge it.
2. **Traceable deferred side effects.** Raw `transaction.on_commit(...)` is
   invisible at runtime. The logged `on_commit` wrapper records when a callback
   is scheduled and when it runs, which NFR10 needs to explain "why did the
   email go out 200 ms after the response?".
3. **Proof of rollback.** Failure-injection unit tests that deliberately break
   a step mid-flow and assert that **zero** partial state survives.
4. **A consistency audit.** `tools/integrity_audit.sql` that NFR9 runs after a
   soak to prove the database is globally consistent.

This is the honest framing for the viva: *NFR8 did not make the system atomic
— NFR1 did. NFR8 made the atomicity **explicit, traceable, and provable.***

---

## 3. The three primitives

### 3.1 `atomic_with_isolation(level="read committed")`

A context manager (usable as a decorator — `contextlib.contextmanager` returns
a `ContextDecorator`) that opens a transaction at an explicit isolation level.

```python
@contextmanager
def atomic_with_isolation(level="read committed"):
    key = level.strip().lower()
    if key not in _ISOLATION_LEVELS:
        raise ValueError(f"unknown isolation level {level!r}; ...")
    sql_level = _ISOLATION_LEVELS[key]          # whitelist → safe to interpolate
    is_outermost = not connection.in_atomic_block
    with transaction.atomic():
        if is_outermost and connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute(f"SET TRANSACTION ISOLATION LEVEL {sql_level}")
        yield
```

Two design points that matter:

- **`SET TRANSACTION ISOLATION LEVEL` is only legal as the first statement of
  the outermost transaction.** A nested call becomes a `SAVEPOINT` and inherits
  the surrounding level (PostgreSQL forbids changing it mid-transaction), so the
  helper detects nesting via `connection.in_atomic_block` and skips the `SET`.
- **The level is whitelisted, not interpolated raw.** The value cannot be a
  bound parameter in a `SET` statement, so an allow-list (`read committed` /
  `repeatable read` / `serializable`) is the safe way to build the SQL.

### 3.2 `on_commit(callback, **kwargs)` — logged

```python
def on_commit(callback, **kwargs):
    bound = functools.partial(callback, **kwargs) if kwargs else callback
    label = getattr(callback, "__name__", repr(callback))
    def _run_logged():
        logger.info("tx.on_commit.run", extra={"callback": label})
        bound()
    logger.debug("tx.on_commit.scheduled", extra={"callback": label})
    transaction.on_commit(_run_logged)
```

This is the seam between **critical** work (inside the transaction) and
**secondary** work (after commit). The lecture point: a side effect that
escapes the database — a Celery task, an email, a cache write — must run only
**after** the commit succeeds, or a rolled-back order produces an orphan
invoice. `on_commit` enforces that and logs both halves so NFR10 can see it.

### 3.3 `run_saga(steps)` — and why it is deliberately not on the hot path

```python
def run_saga(steps):
    completed = []
    try:
        for step in steps:
            step.action()
            completed.append(step)
    except Exception:
        for step in reversed(completed):
            try:
                step.compensation()
            except Exception:
                logger.exception("saga.compensation_failed")
        raise
```

A saga is the answer when a workflow spans systems that **cannot share one DB
transaction** (e.g. an external payment gateway): there is no `ROLLBACK`, so you
run forward actions and, on failure, walk back the completed ones with
compensations.

**This project does not need it yet — and saying so is the point.** Payment is
settled against an in-DB simulated wallet (`Customer.wallet_balance`), so every
effect of `capture_payment` is a row write inside one transaction.
`atomic_with_isolation` already gives true all-or-nothing. `run_saga` is shipped
and unit-tested as the **correct, ready pattern** for the day a real gateway
(whose charge escapes our database) is introduced — at which point the wallet
debit becomes a saga step with a "refund" compensation. Building it now without
need would be complexity for its own sake; understanding *when* it applies is
the deeper result.

---

## 4. Atomicity invariants per composite operation

For each operation, every listed write lives inside **one**
`atomic_with_isolation` block; anything marked `on_commit` runs **after** the
commit and never on the rollback path.

```
place_order  (READ COMMITTED)
  LOCK   Cart                 (FOR UPDATE)         -- serialize multi-tab checkout
  INSERT Order
  INSERT OrderItems           (bulk, price snapshot)
  UPDATE StockItem.reserved   (per item, FOR UPDATE, PK-ASC order)
  INSERT StockMovement        (reserve, per item)
  UPDATE Cart.status -> CHECKED_OUT
  on_commit: invoice + confirmation dispatch        [AFTER commit only]

capture_payment  (READ COMMITTED)
  LOCK   PaymentIntent        (FOR UPDATE)
  LOCK   Order                (FOR UPDATE)
  LOCK   Customer (wallet)    (FOR UPDATE)
  UPDATE StockItem.on_hand/reserved  (consume, per item, FOR UPDATE)
  INSERT StockMovement        (consume)
  UPDATE Customer.wallet_balance  (- amount)
  UPDATE PaymentIntent -> CAPTURED
  UPDATE Order -> PAID

refund_payment  (READ COMMITTED)
  LOCK   PaymentIntent, Order, Customer  (FOR UPDATE)
  UPDATE StockItem.on_hand     (restock, per item)
  INSERT StockMovement         (inbound)
  UPDATE Customer.wallet_balance  (+ amount)
  UPDATE PaymentIntent -> REFUNDED
  UPDATE Order -> CANCELLED

cancel_order  (READ COMMITTED)
  LOCK   Order                 (FOR UPDATE) + status guard
  UPDATE StockItem.reserved    (release, per item)
  INSERT StockMovement         (release)
  UPDATE Order -> CANCELLED
```

The decisive property: in `capture_payment`, the wallet debit, the intent
transition, the order transition, and the stock consume are one unit. Money
never moves without stock moving, and an order is never marked `PAID` without
the wallet actually being charged.

---

## 5. Isolation-level decision (the defensible choice)

**Every composite operation runs at `READ COMMITTED`** — PostgreSQL's default,
now stated explicitly in the code.

Why not escalate to `REPEATABLE READ` or `SERIALIZABLE`?

> The contended rows are already serialized by **explicit pessimistic locks**
> (`SELECT ... FOR UPDATE` on `StockItem`, `PaymentIntent`, `Order`, the
> `Customer` wallet, and the `Cart`). Because the read-modify-write on every
> hot row happens *while that row is locked*, no concurrent transaction can
> interleave on it. Escalating the isolation level would add nothing to
> correctness here, while forcing PostgreSQL to raise `serialization_failure`
> under contention — which then demands an application retry loop and costs
> throughput. So the right choice is the **lowest** isolation level plus
> explicit row locks, not a higher level.

When escalation *would* be correct (documented so the choice is principled, not
lazy):

- **`REPEATABLE READ`** for a read-modify-write that cannot take a row lock
  (e.g. a decision based on an aggregate over many rows).
- **`SERIALIZABLE`** for a cross-row invariant that no single lock can protect —
  and only ever with an explicit retry loop around `serialization_failure`.

`atomic_with_isolation` exists precisely so this decision is visible in review
instead of buried inside a bare `@transaction.atomic`.

---

## 6. Proof of rollback — failure-injection tests

The acceptance criterion is "inject a failure mid-flow and assert zero partial
state." Two tests do exactly that.

**`test_place_order_rolls_back_completely_when_reservation_fails`** patches the
inventory reservation — which runs *after* the `Order` and `OrderItems` are
already inserted — to raise. It then asserts:

```python
assert Order.objects.count() == 0
assert OrderItem.objects.count() == 0
assert StockMovement.objects.count() == 0
assert Cart.objects.get(customer=customer).status == Cart.OPEN  # not CHECKED_OUT
assert stock.on_hand == 10 and stock.reserved == 0
```

**`test_capture_rolls_back_every_write_after_a_late_failure`** is stronger: it
lets `consume_stock` perform a **real** write to `StockItem` + `StockMovement`,
*then* raises. It asserts the whole composite rolled back anyway:

```python
assert intent.status == PaymentIntent.INIT     # not CAPTURED
assert order.status  == Order.PENDING          # not PAID
assert stock.on_hand == 5 and stock.reserved == 1   # consume undone
assert StockMovement.objects.count() == 0           # ledger row undone
assert customer.wallet_balance == Decimal("200.00") # wallet not debited
```

Because these tests pass against both the pre-refactor (`@transaction.atomic`)
and post-refactor (`atomic_with_isolation`) code, they double as the **safety
net** proving the refactor changed the mechanism without changing behaviour.

The saga and isolation primitives were built strictly test-first (the tests
failed with `NotImplementedError`, then passed once implemented).

---

## 7. Post-run integrity audit — `tools/integrity_audit.sql`

A single query that returns **one row per integrity violation**; a healthy
system returns **zero rows**. NFR9 runs it after the 100-user soak to prove the
run left no inconsistent state. The six checks:

| # | Check | Catches |
|---|---|---|
| 1 | `negative_or_impossible_stock` | `on_hand<0`, `reserved<0`, or `reserved>on_hand` (oversell / phantom reservation) |
| 2 | `paid_order_without_capture` | a `paid` order with no `captured` payment (order paid, money not taken) |
| 3 | `captured_payment_on_unpaid_order` | a `captured` intent whose order never advanced past `pending` |
| 4 | `double_capture_on_order` | more than one `captured` intent per order (double charge) |
| 5 | `fulfilled_order_without_items` | a `paid`/`shipped`/`delivered` order with no line items |
| 6 | `refunded_payment_with_live_order` | a `refunded` intent whose order is not `cancelled` |

Validated two ways:

```text
# clean DB → no violations
$ psql ... -f tools/integrity_audit.sql
 check_name | ref | detail
------------+-----+--------
(0 rows)

# a planted impossible-stock row IS flagged
          check_name          |        ref        |        detail
------------------------------+-------------------+----------------------
 negative_or_impossible_stock | stock_item_id=999 | on_hand=2 reserved=7
(1 row)
```

---

## 8. How to verify locally

```bash
# 1. Bring up Postgres (+ Redis for the on_commit dispatch in the wider suite)
docker compose up -d db redis

# 2. Run the NFR8 unit tests
docker compose run --rm web1 pytest tests/unit/test_atomic_saga.py \
    tests/unit/test_atomic_isolation.py \
    tests/unit/test_atomic_place_order.py \
    tests/unit/test_atomic_capture_payment.py -q

# 3. Run the whole unit suite (proves no regression)
docker compose run --rm web1 pytest tests/unit -q
#   → 37 passed

# 4. Run the integrity audit against the DB
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -f /app/tools/integrity_audit.sql   # 0 rows = consistent
```

Verified result: **37 passed** in the full `tests/unit` suite (the four new
`test_atomic_*` files plus every pre-existing test, with no regressions).

---

## 9. Mapping to the course material

| Lecture concept | Where it shows up in NFR8 |
|---|---|
| ACID atomicity | `atomic_with_isolation` makes each composite operation one all-or-nothing unit |
| Isolation levels | Explicit `READ COMMITTED`, with a written rationale for not escalating |
| Pessimistic locking | `FOR UPDATE` on every contended row is *why* READ COMMITTED is sufficient (link to NFR1/NFR7) |
| Critical vs. secondary work | `on_commit` defers email/invoice/cache out of the transaction (link to NFR3) |
| Compensating transactions / SAGA | `run_saga` — built, tested, and correctly scoped to "only when an external system escapes the DB" |
| Rollback / failure recovery | Failure-injection tests prove zero partial state on any mid-flow error |
| Data consistency | `integrity_audit.sql` asserts global invariants after a load run (link to NFR9) |

---

## 10. Honest caveats

- The wallet is a **simulated** in-DB balance, so this project never exercises
  the saga on the hot path. The saga is verified in isolation, not end-to-end
  against a real gateway.
- `atomic_with_isolation` only emits `SET TRANSACTION ISOLATION LEVEL` on
  PostgreSQL; on other backends it degrades to a plain `transaction.atomic`
  (the project targets PostgreSQL, so this is a portability note, not a gap).
- The integrity audit asserts state-machine invariants (status ↔ payment ↔
  stock); it does **not** reconcile the full `StockMovement` ledger sum against
  `on_hand` (that belongs to the NFR4 batch reconciliation).
