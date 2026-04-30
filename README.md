# High-Performance E-Commerce Backend Engine

> Parallel Programming course project — Spring 2026.
> A backend engine for an e-commerce platform engineered around the
> non-functional requirements of performance, concurrency, data integrity,
> resource governance, and background automation.

---

## 1. Project goal

Build a Django + DRF service that exposes a basic e-commerce API (users,
products, cart, orders, inventory, payments) and uses it as a **vehicle for
applying ten non-functional requirements** (NFRs). The grade is driven by
NFR coverage and behaviour under load, not by feature breadth.

The single most important question this codebase must answer is:

> "Can we serve 100+ concurrent users without losing data, oversetting
>  inventory, or collapsing under contention?"

Everything below is organized to make that answer demonstrable.

---

## 2. Stack and rationale

| Layer | Tool | Why this choice |
|---|---|---|
| Web framework | Django 5 + DRF | Mature ORM, well-documented transaction semantics |
| Database | PostgreSQL 16 | Real `SELECT ... FOR UPDATE`, MVCC, isolation levels — required by NFR7 / NFR8 |
| Cache / broker | Redis 7 | TTLs, atomic ops, shared across instances — required by NFR6 |
| Async queue | Celery 5 | Decouple long I/O (PDF, email) from the request path — NFR3 / NFR4 |
| App server | Gunicorn (sync workers) | Explicit cap on concurrent requests — NFR2 |
| Reverse proxy / LB | Nginx | Two backends behind least_conn — NFR5 |
| Load generator | Locust | Drives 100+ virtual users — NFR9 |
| Profiling | django-silk + Locust stats | Before/after measurement — NFR10 |
| Containers | Docker + docker-compose | Reproducible topology |

---

## 3. Repository layout

```
.
├── config/                  # Django + Celery configuration
│   ├── settings/{base,dev,prod}.py
│   ├── celery.py
│   └── urls.py
├── core/                    # Cross-cutting concerns (AOP + shared infra)
│   ├── aop/                 # Decorators + middleware for instrumentation
│   ├── concurrency/         # [NFR1] shared-data protection helpers
│   ├── resources/           # [NFR2] semaphores / bounded thread pool
│   ├── cache/               # [NFR6] Redis cache layer
│   ├── batch/               # [NFR4] chunked batch primitives
│   ├── transactions/        # [NFR8] ACID helpers
│   └── benchmarking/        # [NFR10] before/after measurement
├── apps/                    # Feature-based business modules
│   ├── users/
│   ├── products/
│   ├── cart/
│   ├── orders/
│   ├── inventory/           # ← highest-contention module
│   └── payments/
├── tasks/                   # Celery tasks (async + batch)
│   ├── notifications.py     # [NFR3]
│   ├── invoicing.py         # [NFR3]
│   └── daily_sales_batch.py # [NFR4]
├── tests/
│   ├── unit/
│   └── stress/
│       └── locustfile.py    # [NFR9]
├── docker/
│   ├── Dockerfile
│   ├── nginx.conf           # [NFR5]
│   └── entrypoint.sh
├── docs/
│   ├── ARCHITECTURE.md
│   ├── AOP.md
│   ├── CONCURRENCY_POINTS.md
│   ├── DEVELOPER_GUIDE.md
│   ├── requirements/        # 10 NFR specs (one per file)
│   └── assignments/         # 5 developer task sheets
├── docker-compose.yml
├── manage.py
├── requirements.txt
└── requirements-dev.txt
```

Each app contains the same internal layout: `models.py`, `serializers.py`,
`services.py` (business logic + concurrency control), `views.py`, `urls.py`,
`admin.py`.

---

## 4. NFR ownership map

Each developer owns **one cross-cutting NFR** that they implement across
every relevant feature (orders, inventory, products, ...). This mirrors how
NFRs behave in real systems: they are *not* feature-local.

| # | Requirement | Owner | Status | Spec |
|---|---|---|---|---|
| 1 | Concurrent access / race-condition protection | **Dev 1** | stubs ready | [docs/requirements/01-concurrent-access.md](docs/requirements/01-concurrent-access.md) |
| 2 | Resource management | **Dev 2** | stubs ready | [docs/requirements/02-resource-management.md](docs/requirements/02-resource-management.md) |
| 3 | Async queues | **Dev 3** | stubs ready | [docs/requirements/03-async-queues.md](docs/requirements/03-async-queues.md) |
| 4 | Batch processing | **Dev 4** | stubs ready | [docs/requirements/04-batch-processing.md](docs/requirements/04-batch-processing.md) |
| 5 | Load distribution | **Dev 5** | stubs ready | [docs/requirements/05-load-distribution.md](docs/requirements/05-load-distribution.md) |
| 6 | Distributed caching | _unassigned_ | stubs ready | [docs/requirements/06-distributed-caching.md](docs/requirements/06-distributed-caching.md) |
| 7 | Concurrency control (locking) | _unassigned_ | stubs ready | [docs/requirements/07-concurrency-control.md](docs/requirements/07-concurrency-control.md) |
| 8 | ACID transactions | _unassigned_ | stubs ready | [docs/requirements/08-acid-transactions.md](docs/requirements/08-acid-transactions.md) |
| 9 | Stress testing | _unassigned_ | stubs ready | [docs/requirements/09-stress-testing.md](docs/requirements/09-stress-testing.md) |
| 10 | Benchmarking and bottleneck analysis | _unassigned_ | stubs ready | [docs/requirements/10-benchmarking.md](docs/requirements/10-benchmarking.md) |

Per-developer task sheets live in [docs/assignments/](docs/assignments/).

---

## 5. Running the project

```bash
cp .env.example .env
docker-compose up --build
docker-compose exec web1 python manage.py seed_demo --fresh
```

Endpoints:
- API (via Nginx LB) → http://localhost
- Direct Django (debug) → http://localhost:8001 and http://localhost:8002
- Flower (Celery UI) → http://localhost:5555
- Locust (stress test UI) → http://localhost:8089

External API testing: Postman collection + environment in
[tools/postman/](tools/postman/) — see [tools/postman/README.md](tools/postman/README.md).

Seeder produces ~17k rows (categories, products, customers, orders, stock
movements, payments). Default password for every seeded user is
`Password123!`; tokens are auto-issued for `user0001`..`user0050`.

Full developer setup: see [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md).

---

## 6. Required deliverables

- Architecture write-up + AOP description → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/AOP.md](docs/AOP.md)
- Map of every concurrency point in the code → [docs/CONCURRENCY_POINTS.md](docs/CONCURRENCY_POINTS.md)
- Mapping from course Sessions 1–4 to project artefacts → [docs/LECTURE_MAPPING.md](docs/LECTURE_MAPPING.md)
- Stress-test report (NFR9) and benchmark report (NFR10) — to be generated
  after their owners finish implementation.
