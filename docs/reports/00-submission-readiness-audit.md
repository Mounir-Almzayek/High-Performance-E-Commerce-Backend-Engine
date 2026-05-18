# Submission Readiness Audit

> Date checked: 2026-05-17
> Submission date to target: Monday, 2026-05-18

This checklist compares the current repository against the instructor's
latest notes. It separates what is already covered from what still needs
evidence, implementation, or screenshots before submission.

---

## Executive status

| Area | Status | Notes |
|---|---|---|
| Payment simulation + wallet | Implemented, evidence needed | `Customer.wallet_balance` is checked/deducted during capture; demo screenshots still needed |
| Race condition before/after | Partially covered | Concurrency-safe code and unit tests exist; before/after web/JMeter evidence is missing |
| Resource management | Code covered, evidence missing | Named pools, caps, and process diagnostics exist; CPU/RAM/thread screenshots are still needed |
| Async/background processing | Mostly covered | Celery + Redis implemented; report needs explicit async-vs-queue comparison and failure demo screenshots |
| Queue/message broker | Covered | Redis + Celery are acceptable for this project |
| Background failure / circuit-breaker concept | Partially covered | Retries/idempotency exist; need a simple failure demo showing no infinite loop |
| Testing tools/artifacts | JMX ready, evidence missing | JMeter `.jmx` plans exist; result screenshots still need to be captured |
| 100-user system test | Scenario ready, run evidence missing | `tests/stress/locustfile.py` covers browse, checkout, webhook replay, and resource stress; 100-user screenshots/HTML are still needed |
| Reports | Partially covered | NFR1-NFR4 reports exist; screenshots and measured before/after results are still needed |
| Packaging | Repo format OK | Do not submit only as one ZIP file |

---

## 1. Payment and wallet

Current state:

- `apps/payments/models.py` has `PaymentIntent`.
- `apps/payments/services.py::capture_payment` locks the payment intent,
  marks it captured, transitions the order to paid, and consumes stock.
- `apps/users/models.py` has `Customer.wallet_balance`.
- `capture_payment` locks the customer row, checks balance, simulates an
  optional provider delay, and deducts the amount on success.
- Insufficient balance raises a 402-safe `InsufficientWalletBalance`
  response and leaves payment/order/stock unchanged.
- Refund credits the wallet back.

Required evidence:

- Screenshot/API trace for successful capture with balance deduction.
- Screenshot/API trace for insufficient balance rejection.
- Optional demo delay by setting
  `PAYMENT_CAPTURE_SIMULATED_DELAY_SECONDS=2`.

Status: **implementation ready, screenshots still needed**.

---

## 2. Race condition before/after

Current state:

- NFR1 code is strong:
  - inventory uses row locks
  - cart checkout locks the cart row
  - payments lock payment/order rows
  - loyalty points use atomic `F()` updates
- Unit tests demonstrate the fixed behavior:
  - `tests/unit/test_concurrency_inventory.py`
  - `tests/unit/test_concurrency_loyalty.py`
  - `tests/unit/test_concurrency_payments.py`
- `docs/reports/01-nfr1-implementation.md` explains why the solution is
  correct.

Missing evidence:

- A concrete "before" run showing the race.
- A concrete "after" run showing the fixed behavior.
- JMeter `.jmx` file.
- Screenshot of JMeter results.

Recommended demo:

- Endpoint: same checkout/payment or same stock reservation endpoint.
- Before version: temporarily run a deliberately unsafe endpoint or
  branch/commit where the lock is removed.
- After version: current locked implementation.
- JMeter: many simultaneous requests against the same product/order.
- Report:
  - before: oversold stock or inconsistent successful purchases
  - after: exactly allowed purchases succeed; the rest fail cleanly

Status: **code ready, evidence missing**.

---

## 3. Resource management / thread pool

Current state:

- `core/resources/pool.py` implements:
  - named capacity pools
  - `resource_slot`
  - `capacity_limited`
  - `bounded_executor`
  - live counters
- `config/settings/base.py` exposes env-driven caps:
  - Gunicorn workers/threads
  - Celery concurrency
  - checkout/payment/batch/internal pool caps
- `core/diagnostics/views.py` exposes `GET /api/v1/_diag/pool/`.
- `core/diagnostics/views.py` also exposes `GET /api/v1/_diag/process/`
  for process CPU/RAM/thread evidence.
- Checkout, payments, and batch processing use the resource caps.
- Unit tests cover pool behavior.

Missing evidence:

- Before/after load run.
- CPU/RAM/thread/process/connection monitoring.
- Screenshots or graphs.
- Comparison with different worker/thread settings.

Recommended demo:

- Run the same load with low and normal caps:
  - `GUNICORN_WORKERS=1`, `GUNICORN_THREADS=1`
  - then `GUNICORN_WORKERS=4`, `GUNICORN_THREADS=2`
- Hit product search/list, add-to-cart, checkout, and payment.
- Capture:
  - p95/p99 latency
  - failures
  - CPU
  - RAM
  - process/thread count
  - DB connections if possible
  - `/api/v1/_diag/pool/` before/during/after
  - `/api/v1/_diag/process/` before/during/after

Recommended monitoring:

- Best: Prometheus + Grafana.
- Acceptable fallback: Docker stats screenshots + Postgres connection
  query + diagnostic endpoint snapshots, but this is weaker.

Status: **implementation good, measurement not ready**.

---

## 4. Comparing resource-management settings

Current state:

- Gunicorn and Celery settings are env-driven, so the framework supports
  this comparison cleanly.

Recommended comparison table:

| Run | Workers | Threads | Checkout cap | Payment cap | p95 | CPU | RAM | Failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Low capacity | 1 | 1 | 2 | 2 | TBD | TBD | TBD | TBD |
| Balanced | 4 | 2 | 8 | 8 | TBD | TBD | TBD | TBD |
| Too high | 8 | 4 | 16 | 16 | TBD | TBD | TBD | TBD |

Status: **ready to run, numbers missing**.

---

## 5. Async execution vs asynchronous queue

Current state:

- The project uses a real asynchronous queue:
  - Celery workers
  - Redis broker/result backend
  - Flower for visibility
- Checkout dispatches invoice/email after commit.
- Invoice/email tasks are retry-aware and idempotent.

Required explanation:

- Asynchronous execution:
  - starts work outside the immediate call path
  - may be a thread, coroutine, or background process
  - does not automatically persist work if the process dies
  - good for low-value or easily repeatable work such as lightweight logs
- Asynchronous queue:
  - stores a message in a broker
  - workers consume later
  - supports retry, observability, and crash recovery
  - better for important side effects such as invoices and emails

Cost comparison:

| Option | Benefit | Cost |
|---|---|---|
| Plain async execution | Simple and low latency | Can lose work on process crash; weak retry story |
| Queue/message broker | Durable, retryable, observable | Adds broker, workers, serialization, idempotency concerns |

Status: **implementation good, report should explicitly include this explanation**.

---

## 6. Queue / message broker

Current state:

- Redis is used as Celery broker and result backend.
- This is acceptable based on the instructor note.

Status: **covered**.

---

## 7. Background failure / circuit-breaker concept

Current state:

- Celery tasks use retry policies.
- `generate_invoice` has `max_retries=3`.
- `send_order_confirmation` has `max_retries=5`.
- Idempotency guards prevent duplicate invoice/email effects.

Missing evidence:

- A simple failure demo:
  - trigger task
  - cause provider/connection failure or kill worker mid-task
  - restart worker
  - show it retries finitely and does not loop forever
  - show no duplicate email/invoice state

Status: **mechanism exists, demo evidence missing**.

---

## 8. Testing approach

Current state:

- Locust includes browse, checkout, webhook replay, and resource-stress
  flows.
- Unit tests exist for concurrency and resource pool.
- JMeter files exist under `tools/jmeter/`.

Required:

- For the instructor's JMeter request, include:
  - `.jmx`
  - screenshot of results
- Scripts are acceptable for some tests, but this does not replace the
  explicit JMX + screenshot requirement if requested.
- JMeter plans now live under `tools/jmeter/`; screenshots should go
  under `docs/reports/assets/`.

Status: **JMX structure ready; real result screenshots still missing**.

---

## 9. 100-user full-system test

Current state:

- Docker Compose has a Locust service.
- `tests/stress/locustfile.py` implements the main flows needed for a
  100-user run.

Required evidence:

- Run:
  - login/token auth
  - product browsing
  - add to cart
  - place order
  - create/capture payment
  - webhook replay/resource-stress scenarios when needed
- Run 100 users against `http://nginx`.
- Store results in a report with screenshots/CSV graphs.

Status: **scenario ready; run evidence missing**.

---

## 10. Tools and monitoring

Current state:

- Locust service exists.
- Flower exists.
- django-silk exists.
- `/api/v1/_diag/process/` exposes application-level CPU/RAM/thread
  data.
- No Prometheus/Grafana services exist.

Recommended:

- Add Prometheus/Grafana if time allows.
- If not, capture:
  - Locust charts/screenshots
  - Flower screenshots
  - Docker stats screenshots
  - Postgres connection counts
  - `/api/v1/_diag/pool/` snapshots

Status: **basic tools exist; screenshot evidence still needs capture**.

---

## 11. JMeter deliverables

Current state:

- JMeter plans exist under `tools/jmeter/`.
- No result screenshots found.

Required:

- Add screenshots under something like `docs/reports/assets/`.
- Reference both from the relevant report.

Status: **JMX files ready; screenshots still missing**.

---

## 12. Reports and screenshots

Current state:

- Reports exist for NFR1-NFR4.
- Reports now reference the expected image files under
  `docs/reports/assets/`.

Required:

- Add screenshots for:
  - race before/after
  - resource monitoring before/after
  - async/queue timing before/after
  - Flower retry/failure behavior
  - 100-user full-system run

Status: **reports are structurally good; evidence artifacts missing**.

---

## 13. Submission date

Use the exact date:

- **Monday, 2026-05-18**

Status: **noted**.

---

## 14. Upload format

Do not submit only one compressed ZIP. The safer interpretation is:

- submit as a repository or normal project files
- keep reports, scripts, JMX files, screenshots, and source code in the
  repo structure

Status: **repo format is appropriate**.

---

## 15. Open questions not answered by instructor text

The instructor text does not clearly answer:

- whether the report must be inside the repo
- whether one script can replace all tools for all five requests
- how to correct a submission that was uploaded as a ZIP

Recommended choice:

- Put reports inside the repo anyway.
- Use scripts where allowed, but still provide JMX + screenshots where
  explicitly requested.
- Avoid ZIP-only submission.

---

## Highest-priority next fixes

1. Capture JMeter result screenshots for NFR1, NFR2, NFR3, and NFR5.
2. Capture resource monitoring screenshots for NFR2 using Docker stats
   plus `/api/v1/_diag/pool/` and `/api/v1/_diag/process/`.
3. Add async-vs-queue and failure demo screenshots for NFR3.
4. Run the 100-user Locust test and save the HTML/CSV/screenshots.
5. Run migrations/tests inside the Docker environment.
6. Add the result images/assets to the reports before submission.
