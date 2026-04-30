# Developer Guide

## 1. Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- ~4 GB RAM available to Docker
- Git

No local Python install is required — everything runs in containers. If
you want a local virtualenv for IDE support:

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # Linux/macOS
pip install -r requirements-dev.txt
```

---

## 2. First-run

```bash
cp .env.example .env
docker-compose up --build
```

Wait until you see `gunicorn: Listening on 0.0.0.0:8000` for both `web1`
and `web2`. Then in another terminal:

```bash
docker-compose exec web1 python manage.py createsuperuser
docker-compose exec web1 python manage.py seed_demo --fresh
```

`seed_demo` populates a medium-sized realistic dataset (~17k rows) in
under a minute. Default password for every seeded user is `Password123!`,
and the first 50 users (`user0001` .. `user0050`) get DRF auth tokens
auto-issued so Postman and Locust can pick one and run immediately.

Useful seeder flags:

| Flag | Effect |
|---|---|
| `--fresh` | Truncate seeded tables first (destructive). |
| `--products N` / `--customers N` / `--orders N` | Override default sizes. |
| `--seed N` | Change RNG seed for reproducibility (default 42). |

You can now reach:

| Endpoint | URL |
|---|---|
| API (load-balanced) | http://localhost/api/v1/ |
| Direct web1 | http://localhost:8001/api/v1/ |
| Direct web2 | http://localhost:8002/api/v1/ |
| Admin | http://localhost/admin/ |
| Silk profiler | http://localhost/silk/ |
| Flower (Celery) | http://localhost:5555/ |
| Locust | http://localhost:8089/ |

External API testing: import [tools/postman/ecommerce_engine.postman_collection.json](../tools/postman/ecommerce_engine.postman_collection.json)
and the matching environment file. The collection's `Auth → Token Login`
request captures the auth token automatically into a collection variable;
every other request inherits bearer auth from there.

---

## 3. Common workflows

### Run migrations

```bash
docker-compose exec web1 python manage.py makemigrations
docker-compose exec web1 python manage.py migrate
```

(`makemigrations` only on the developer's machine; the container restart
runs `migrate` automatically via `entrypoint.sh`.)

### Run unit tests

```bash
docker-compose exec web1 pytest
```

### Open a Django shell

```bash
docker-compose exec web1 python manage.py shell
```

### Tail logs for one service

```bash
docker-compose logs -f web1
docker-compose logs -f celery_worker
```

### Reset the database

```bash
docker-compose down -v          # destructive: removes pgdata volume
docker-compose up --build
```

---

## 4. Coding conventions

- **Business logic lives in `services.py`.** Views are thin: validate
  input, call a service, serialize the result.
- **Every concurrency point goes through `core/concurrency/locks.py`.**
  No raw `threading.Lock`. No `select_for_update` in views.
- **Side effects fire after commit.** Never inside the transaction.
  Use `transaction.on_commit` (or the `core/transactions/atomic.on_commit`
  helper once it lands).
- **Cache writers invalidate.** Any service function that mutates a cached
  entity must call the corresponding `core.cache.redis_cache.invalidate_*`
  helper.
- **Decorate services, not views.** `@timed` and `@audit_log` go on the
  service function so the timing window covers the locks.
- **Lock in PK ASC order.** Whenever you lock more than one row, sort
  identifiers ascending before acquiring the locks.

---

## 5. Adding a new endpoint

1. Add the model field / new model under the relevant `apps/<feature>/models.py`.
2. Add a serializer.
3. Add a `services.<verb>(...)` function that owns the business logic and
   the concurrency control.
4. Add the view (call the service from `perform_create` / an `@action`).
5. Wire the URL in the app's `urls.py`.
6. If the change introduces a new race, update `docs/CONCURRENCY_POINTS.md`.
7. Add a unit test under `tests/unit/`. If the change is performance-
   sensitive, also add a Locust task under `tests/stress/locustfile.py`.

---

## 6. Submitting a milestone

The course requires interim review sessions. Before each one:

- All migrations are committed and apply cleanly on a fresh DB.
- `pytest` is green.
- The relevant `docs/requirements/<n>-*.md` is updated with what
  changed and why.
- A short demo script (curl / HTTPie) is added to that doc to walk the
  examiner through the behaviour.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `OperationalError: too many connections` | Gunicorn workers x instances x CONN_MAX_AGE > pg `max_connections` | Lower `GUNICORN_WORKERS` in `.env` or raise pg's limit |
| Webhook gets processed twice | Missing UNIQUE on `WebhookEvent.signature` or capture not wrapped in lock | See `apps/payments/services.py` |
| Locust shows huge p99 only on web1 | Nginx is not balancing — check `X-Served-By` header | Verify `least_conn` in `docker/nginx.conf` |
| `IntegrityError` on `add_item` | Cart-product unique constraint hit by retried request | Service should upsert, not insert |
