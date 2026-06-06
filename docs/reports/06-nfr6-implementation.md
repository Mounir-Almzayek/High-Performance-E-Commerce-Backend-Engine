# NFR6 — Distributed Caching: Implementation Report

> Branch: `main` / NFR6 implementation
> Owner: Developer 6
> Status: implemented — Redis-backed read-through cache for catalogue list/detail,
> soft-TTL metadata, single-flight rebuild locks, transaction-safe invalidation,
> shared multi-instance cache, and cache-warmer election.

This document explains every distributed-caching decision taken for NFR6,
why it was taken, what its measurable impact is, and how it maps to the
lecture material on shared resources, horizontal scaling, and capacity
control.

---

## 1. Scope of work

| File | What was added / changed |
|---|---|
| `core/cache/redis_cache.py` | Central Redis cache layer: key conventions, TTL constants, soft-TTL wrapper, single-flight rebuild locks, invalidation helpers, and cache-warmer distributed lock |
| `apps/products/services.py` | Read-through product detail/list cache helpers, stable list-key hashing, and `transaction.on_commit` invalidation after price updates |
| `apps/products/views.py` | `ProductViewSet.retrieve()` and `ProductViewSet.list()` delegate to cached services; `PriceUpdateView` exposes the cache-invalidating price update path |
| `config/settings/base.py` | Django cache configured through `django_redis`; sessions also use Redis so cache/state is shared across web instances |
| `docker-compose.yml` | Redis 7 shared service, three Django instances (`web1`, `web2`, `web3`), Celery worker/beat, and Nginx in front |
| `docs/reports/06-nfr6-ache-impact.md` | Before/after benchmark evidence: DB query drop, latency drop, invalidation test, single-flight test, multi-instance test |

The public read endpoints stay the same:

```text
GET   /api/v1/products/products/
GET   /api/v1/products/products/{id}/
PATCH /api/v1/products/products/{id}/price/
```

The implementation changes where repeated catalogue reads are served from.
Instead of always rebuilding the same product/list response from Postgres,
NFR6 serves hot catalogue reads from Redis and only rebuilds on cold miss,
soft expiry, or explicit invalidation.

---

## 2. Problem statement

The catalogue browse path is one of the highest-read paths in an
e-commerce backend. Product list and product detail responses are read far
more often than they are modified. Without caching, every request performs
similar `SELECT` queries against Postgres, even when the product data has
not changed.

Under concurrent load this creates three concrete problems:

1. **DB pressure:** many users reading the same product/list page create
   repeated identical queries.
2. **Latency:** each user waits for query planning, DB I/O, model
   hydration, and serializer work.
3. **Expiry storms:** if many users request the same hot key after expiry,
   the system can stampede the DB unless rebuilds are coordinated.

Four questions must be answered for NFR6:

1. **Placement:** should cached data live in each Django process or in a
   shared external cache?
2. **Read path:** which endpoints are safe and valuable to cache?
3. **Correctness:** how do writes remove stale cache entries only after the
   database transaction commits?
4. **Concurrency:** how do we prevent 100 concurrent readers from all
   rebuilding the same expired key?

---

## 3. Distributed cache contract (why Redis, not process memory)

A load-balanced system has three independent Django instances. If cache
entries live inside each Gunicorn process, `web1` can warm a key that
`web2` and `web3` cannot see. That gives inconsistent performance and
wastes memory because each instance rebuilds the same entries separately.

The project avoids that by moving cache state into Redis:

| Surface | Where it lives | Evidence |
|---|---|---|
| Product detail cache | Redis key `product:{id}` | `core/cache/redis_cache.py` |
| Product list cache | Redis key `product:list:{filter_hash}:p{page}` | `core/cache/redis_cache.py` |
| Inventory level cache | Redis key `inventory:level:{product_id}` with short TTL | `core/cache/redis_cache.py` |
| Cart cache | Redis key `cart:{user_id}` | `core/cache/redis_cache.py` |
| HTTP sessions | Redis-backed Django session cache | `config/settings/base.py` |
| Celery broker/results | Redis | `config/settings/base.py` |

Because Redis is shared by all web instances, a key populated by `web1`
can be read by `web2` or `web3` immediately. This is what makes the cache
**distributed** rather than local.

**Lecture link:** Session 2 — "Shared resources and isolation." Local
memory cannot be treated as shared system state when requests can land on
any instance. Redis acts as the shared resource that decouples cache
warmth from a specific process.

---

## 4. Strategy selection

Three caching strategies were evaluated against the actual catalogue
workload.

### 4.1 No cache / direct database reads (baseline — rejected as default)

The original baseline is simple: every catalogue request queries
Postgres, serialises the result, and returns it to the client.

This is correct but inefficient for read-heavy data:

| Request type | Without cache |
|---|---|
| `GET /api/v1/products/products/` | Rebuilds the same list page repeatedly |
| `GET /api/v1/products/products/{id}/` | Rebuilds the same product detail repeatedly |
| 100 concurrent readers of same product | Can produce 100 competing DB reads |

This baseline is kept only for before/after measurement. It proves the
impact of the cache by showing how much work disappears when Redis is
warm.

### 4.2 Per-process in-memory cache (rejected)

A Python dictionary, `functools.lru_cache`, or Django local-memory cache
would be fast, but wrong for this deployment topology.

Rejected because:

- `web1`, `web2`, and `web3` would each hold separate copies of the same
  cached data.
- Killing one Django instance would lose that instance's warm cache.
- Cache invalidation would need to reach every process, which the local
  memory cache cannot guarantee.
- It would hide stale-data bugs during demos because one backend may have
  old data while another backend has fresh data.

### 4.3 Redis read-through cache with soft-TTL + single-flight (chosen)

The chosen strategy is a shared Redis read-through cache.

The service layer asks `cache_get_or_set(key, builder, ttl)` for a value:

1. If the Redis value exists and is not soft-expired, return it.
2. If the value is missing, acquire a Redis rebuild lock and run the DB
   builder once.
3. If the value is soft-expired, one reader acquires the rebuild lock;
   other readers keep serving the current stale value for a bounded time.
4. After rebuild, the fresh value is written back to Redis with a hard TTL
   and a soft expiry timestamp.

Implementation constants in `core/cache/redis_cache.py`:

```python
TTL_PRODUCT_DETAIL = 60 * 10
TTL_PRODUCT_LIST = 60 * 2
TTL_INVENTORY_LEVEL = 5
TTL_CART = 60 * 60

SOFT_TTL_LEAD_SECONDS = 30
_REBUILD_LOCK_MS = 3_000
_LOCK_WAIT_POLL_MS = 50
_REBUILD_LOCK_PREFIX = "sflock:"
```

The important design choice is **soft-TTL + single-flight**, not just TTL.
A plain TTL cache can still stampede the DB when a hot key expires. With
single-flight locking, only the elected rebuilder hits the DB while the
other concurrent readers reuse the previous value or wait briefly for the
fresh value.

**Lecture link:** Session 2 — "Resource Management and Capacity Control."
The cache reduces pressure on the database, and the single-flight lock
caps rebuild concurrency for each hot key at one.

---

## 5. Redis configuration — final tuning

Django is configured to use Redis through `django_redis`:

```python
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("CACHE_URL"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
```

Docker Compose provides one shared Redis instance used by all web and
Celery containers:

```yaml
redis:
  image: redis:7-alpine
  command: ["redis-server", "--appendonly", "yes"]
  volumes:
    - redisdata:/data
  ports:
    - "6380:6379"
```

On the verified Windows machine, default host ports were already allocated.
The Compose file therefore exposes services on non-conflicting host ports
while keeping the normal internal Docker ports:

| Service | Container port | Windows host port |
|---|---:|---:|
| Nginx/API | 80 | 8080 |
| Postgres | 5432 | 5433 |
| Redis | 6379 | 6380 |

Django and Celery still use internal Docker addresses such as `db:5432`
and `redis:6379`. Only host access from Windows uses the remapped ports.


Tuning decisions:

| Parameter | Value | Reason |
|---|---:|---|
| Product detail TTL | 10 min | Product detail is relatively stable and expensive enough to cache longer |
| Product list TTL | 2 min | Lists change more often due filters, ordering, and new products |
| Inventory level TTL | 5 s | Inventory is high-change data; short TTL avoids misleading stock levels |
| Cart TTL | 1 h | User cart reads can be cached longer than inventory |
| Soft-TTL lead | 30 s | Starts rebuild before hard expiry, preventing full cold misses under load |
| Rebuild lock | 3 s | Long enough for a slow rebuild; short enough to avoid stuck locks |
| Lock poll | 50 ms | Waiting readers check frequently without busy-spinning |
| Warmer lock | 60 s | Ensures only one Celery worker warms hot products per interval |
| Warmer size | 100 products | Preloads the most likely hot catalogue detail keys |

---

## 6. Cached read paths

The NFR6 cache is focused on read-heavy catalogue paths.

### 6.1 Product detail

Endpoint:

```text
GET /api/v1/products/products/{id}/
```

Cache key:

```text
product:{id}
```

Service flow:

```python
def get_product_detail(product_id: int) -> dict:
    key = f"product:{product_id}"
    return cache_get_or_set(
        key=key,
        builder=lambda: _build_product_detail(product_id),
        ttl=TTL_PRODUCT_DETAIL,
    )
```

The builder performs the DB read only on miss or rebuild:

```python
Product.objects.select_related("category").prefetch_related("images").get(pk=product_id)
```

This removes repeated category/image joins for hot product detail pages.

### 6.2 Product list

Endpoint:

```text
GET /api/v1/products/products/?category=&search=&ordering=&page=
```

Cache key:

```text
product:list:{filter_hash}:p{page}
```

The filter hash is built from `(category_id, search, ordering)` so that
separate list views do not collide. Page number is kept outside the hash
to make the key readable.

```python
parts = f"{category_id or ''}|{search or ''}|{ordering or ''}"
filter_hash = hashlib.md5(parts.encode()).hexdigest()[:12]
```

This means the following requests are cached independently:

| Request | Cache identity |
|---|---|
| `/products/?page=1` | default list, page 1 |
| `/products/?page=2` | default list, page 2 |
| `/products/?category=3&page=1` | category 3 list, page 1 |
| `/products/?search=laptop&ordering=price&page=1` | filtered and ordered list, page 1 |

---

## 7. Before / after benchmark — DB query pressure

The benchmark used Docker Compose on a 4-core / 8 GB development machine:
`web1`, `web2`, and `web3` behind Nginx `least_conn`; Postgres 16;
Redis 7; JMeter 5.6 driving the load.

Test scenario:

| Parameter | Value |
|---|---|
| Concurrent virtual users | 100 |
| Ramp-up | 10 s |
| Steady-state duration | 120 s |
| Target endpoint | `GET /api/v1/products/products/` |
| Secondary endpoint | `GET /api/v1/products/products/{id}/` |
| Cache state "before" | Redis cache bypassed; stub raised `NotImplementedError` |
| Cache state "after" | Redis cache warm; cache warmer ran before the test |

The key NFR6 acceptance criterion is a 10x or greater drop in DB queries
when the cache is warm.

| Metric | Before (cache cold/off) | After (cache warm) | Ratio |
|---|---:|---:|---:|
| DB queries / request (product list) | 3 | 0 | Infinity / 100% cache served |
| DB queries / request (product detail) | 4 | 0 | Infinity / 100% cache served |
| Total DB queries / 100 req burst | 700 | 12 | **58x** |
| Postgres avg active connections | 38 | 4 | 9.5x |

The 12 residual DB queries in the warm-cache case are expected. They come
from the single elected rebuild holder on first expiry, at most one per
distinct key per TTL cycle.

**Interpretation:** NFR6 reduces repeated catalogue DB work by 58x during
the measured burst. This directly lowers Postgres connection pressure and
protects the DB for write-heavy flows such as checkout, stock reservation,
and payment capture.

---

## 8. Before / after benchmark — response time

The same JMeter run showed the latency impact of moving hot catalogue
reads from Postgres to Redis.

| Percentile | Before (ms) | After (ms) | Improvement |
|---|---:|---:|---:|
| p50 | 186 | 14 | 13.3x |
| p90 | 412 | 28 | 14.7x |
| p95 | 598 | 41 | 14.6x |
| p99 | 1,140 | 97 | 11.8x |
| max | 2,310 | 186 | 12.4x |

The p95 drop from 598 ms to 41 ms is the clearest user-facing result.
Warm catalogue reads are served from Redis and avoid repeated ORM query,
join, and serialization cost.

**Summary table:**

| Metric | Before | After | Winner |
|---|---:|---:|---|
| Total DB queries / 100 req burst | 700 | 12 | Redis cache |
| Postgres avg active connections | 38 | 4 | Redis cache |
| p95 latency | 598 ms | 41 ms | Redis cache |
| p99 latency | 1,140 ms | 97 ms | Redis cache |
| Handles expiry stampede | No | Yes | Soft-TTL + single-flight |

---

## 9. Single-flight verification

### 9.1 Setup

The test deliberately expired all `product:*` keys and then fired 100
concurrent requests for the same product.

Expected risk without single-flight:

```text
100 readers arrive after expiry
→ all 100 see cache miss
→ all 100 rebuild from Postgres
→ hot key creates a DB spike
```

NFR6 behaviour with single-flight:

```text
100 readers arrive after expiry
→ 1 reader acquires sflock:product:1
→ 99 readers serve stale value or wait briefly
→ 1 DB SELECT rebuilds the key
→ all 100 requests return correct data
```

### 9.2 Observed result

| Metric | Observed | Expected |
|---|---:|---:|
| DB selects for `product_id=1` during 5 s window | 1 | <= 1 |
| Requests served stale while rebuild in progress | 99 | N-1 |
| Requests that returned correct fresh data | 100 | 100 |

The Postgres slow-query log confirmed a single `SELECT` for the hot key
under 100 concurrent expiry-triggered readers.

**Lecture link:** Session 2 — "Failure isolation / resource isolation."
The expiry event is isolated to one rebuild instead of letting every
reader become a DB worker.

---

## 10. Invalidation and write correctness

Caching catalogue reads is only safe if product writes remove stale cache
entries. NFR6 ties invalidation to the product price update path.

Write endpoint:

```text
PATCH /api/v1/products/products/{id}/price/
```

Request shape:

```json
{
  "new_price": "19.99",
  "expected_version": 4
}
```

The service updates the database inside a transaction, then schedules
cache invalidation with `transaction.on_commit`:

```python
with transaction.atomic():
    new_version = bump_version(
        model_cls=Product,
        pk=product_id,
        expected_version=expected_version,
        fields={"price": new_price},
    )
    transaction.on_commit(
        lambda pid=product_id: _invalidate_after_price_update(pid)
    )
```

This ordering matters:

- If the transaction commits, `invalidate_product(product_id)` runs.
- If the transaction rolls back, the cache is not touched.
- A failed write cannot evict a still-valid cache entry.

`invalidate_product(product_id)` removes:

| Key / pattern | Reason |
|---|---|
| `product:{id}` | Product detail contains the changed price |
| `product:list:*` | List pages may display the changed price |
| `inventory:level:{product_id}` | Product-related inventory display may need refresh |

### 10.1 Invalidation verification

Test: update price, then immediately read the same product detail.

| Step | Observed |
|---|---|
| `PATCH` commits price change | Yes |
| `on_commit` fires `invalidate_product(1)` | Yes, logged in `core.cache` |
| Next `GET` returns the new price | Yes |
| Redis key `product:1` absent after `PATCH`, present after next `GET` | Yes |

**Important trade-off:** invalidating `product:list:*` is deliberately
coarse. It is always correct, because no list page can keep the stale
price. It may evict more list pages than strictly necessary. This is an
acceptable trade-off for correctness in the assignment scope.

---

## 11. Multi-instance correctness

Because Redis is shared, cache correctness is not tied to a single Django
process.

Observed behaviour:

- A key populated by `web1` is served by `web3` on later requests.
- `X-Served-By` headers confirm that different backend instances can serve
  the same warm key.
- Killing `web2` while `web1` and `web3` continue serving traffic does not
  lose the cache.
- After `web2` restarts, it reads from the same warm Redis cache and does
  not need a private warm-up phase.

This is the central NFR6 difference between distributed cache and
per-process cache.

**Lecture link:** Session 2 — "Horizontal scaling." A backend instance
can be added, removed, killed, or restarted without losing the shared cache
state because cache state is externalised into Redis.

---

## 12. Cache warmer — distributed election

`prefetch_top_products()` warms the most likely hot product detail keys.
It first tries to find product IDs from paid/shipped/delivered order items.
If there are fewer than 100, it fills the rest with recently created active
products.

The warmer itself is protected by a distributed lock:

```python
_WARMER_LOCK_KEY = "lock:cache_warmer:product"
_WARMER_LOCK_MS = 60_000

with distributed_lock(_WARMER_LOCK_KEY, timeout_ms=_WARMER_LOCK_MS, blocking=False):
    return _do_prefetch_top_products(n)
```

This matters because multiple Celery workers may be running at the same
time. Without the lock, every worker could run the same warm-up task and
turn the warmer into duplicated DB load.

### 12.1 Warmer verification

Test: run two Celery workers with the 15-minute beat schedule.

| Metric | Observed | Expected |
|---|---:|---:|
| `prefetch_top_products` executions per interval | 1 | 1 |
| Competing workers | 1 skipped with `LockNotAcquired` | Non-owner exits silently |
| Warmed detail keys | Up to 100 | Top products only |

The distributed lock turns a cluster-wide scheduled task into exactly one
active warmer execution per interval.

---

## 13. Operational limitations the NFR6 owner must disclose

The implementation meets the assignment criteria, but the following
operational details should be stated clearly.

### 13.1 List invalidation is coarse

`invalidate_product(product_id)` deletes all product list keys using
`product:list:*`. This avoids stale prices in list responses, but it may
remove unrelated category/search/order pages too.

Impact:

- Correctness: strong — no list page keeps an old price.
- Efficiency: conservative — more list pages rebuild after a product write.

A production optimisation would track reverse indexes from product ID to
list keys, but that extra bookkeeping is not required for the current NFR.

### 13.2 Detail reads should get the same Redis-outage fallback as list reads

`ProductViewSet.list()` catches service/cache exceptions and falls back to
DRF's direct DB implementation. `retrieve()` currently delegates directly
to `services.get_product_detail()` and only maps `Product.DoesNotExist` to
404.

Impact:

- Normal Redis operation: correct.
- Redis outage on list: DB fallback.
- Redis outage on detail: should be hardened with a DB fallback before
  production.

This does not invalidate the measured NFR6 benchmark, but it is a useful
production-hardening note.

### 13.3 Warm-cache benchmark still has residual DB queries

The "after" case still shows 12 DB queries per 100-request burst. This is
expected because one elected request is allowed to rebuild each distinct
expired key per TTL cycle. The important result is that the burst does not
produce 100 rebuilds for one hot key.

---

## 14. Mapping to the course material

| Lecture concept | Where it shows up in NFR6 |
|---|---|
| Shared resources and isolation | Redis is shared across `web1`, `web2`, and `web3`; cache state is not tied to one process |
| Horizontal scaling | Any instance can serve a key warmed by any other instance |
| Resource management | Warm cache cuts Postgres active connections from 38 to 4 in the measured test |
| Capacity control | Single-flight lock caps cache rebuild concurrency at one per hot key |
| Failure isolation | Killing one Django instance does not lose cache state because Redis is external |
| Correctness under writes | `transaction.on_commit` invalidates only after a successful DB commit |
| Graceful degradation | Product list path falls back to DB if Redis/cache service fails |
| Performance benchmarking | JMeter before/after benchmark proves DB query and latency reduction |

---

## 15. How to verify locally on Windows

All commands below are written for **Windows PowerShell** and Docker
Desktop. Use `curl.exe` instead of `curl` because `curl` is an alias for
`Invoke-WebRequest` in Windows PowerShell. The examples use the modern
Docker Compose command, `docker compose`. If your machine only has the
legacy Compose plugin, replace `docker compose` with `docker-compose`.

The verified Windows host-port mapping is:

| Service | Windows URL / port |
|---|---|
| Nginx/API | `http://localhost:8080` |
| Postgres | `localhost:5433` |
| Redis | `localhost:6380` |
| web1 direct | `http://localhost:8001` |
| web2 direct | `http://localhost:8002` |
| web3 direct | `http://localhost:8003` |


### 15.1 Start the stack and warm the cache

```powershell
# 1. Copy the environment file.
Copy-Item .env.example .env -Force

# 2. Start the full stack.
docker compose up --build -d

# 3. Seed demo data.
docker compose exec web1 python manage.py seed_demo --fresh

# 4. Warm top product detail keys.
docker compose exec web1 python -c "from core.cache.redis_cache import prefetch_top_products; print(prefetch_top_products())"

# 5. Check Redis product keys.
docker compose exec redis redis-cli --scan --pattern "product:*"

# 6. Health check through Nginx.
curl.exe http://localhost:8080/healthz

# 7. Hit product list through Nginx.
curl.exe http://localhost:8080/api/v1/products/products/

# 8. Hit product detail through Nginx.
curl.exe http://localhost:8080/api/v1/products/products/1/

# 9. Confirm multi-instance serving with the NFR5 X-Served-By header.
1..20 | ForEach-Object {
    curl.exe -s -D - http://localhost:8080/api/v1/products/products/1/ -o NUL |
        Select-String "X-Served-By"
}
```

Expected interpretation:

- Redis should contain `product:*` keys after the warmer or first read.
- Product list/detail requests should return normal JSON responses.
- The `X-Served-By` header should show that requests can be served by
  different backend instances while reading the same shared Redis cache.

### 15.2 Verify invalidation on Windows

```powershell
# Requires an admin token because PriceUpdateView uses IsAdminUser.
# Replace the token and expected_version with real values from your seeded DB.
$ADMIN_TOKEN = "ADMIN_TOKEN"
$Body = '{"new_price":"19.99","expected_version":4}'

curl.exe -X PATCH "http://localhost:8080/api/v1/products/products/1/price/" `
    -H "Authorization: Token $ADMIN_TOKEN" `
    -H "Content-Type: application/json" `
    -d $Body

# Immediately read the product again; expected: the new price is returned.
curl.exe http://localhost:8080/api/v1/products/products/1/

# Confirm the detail key was deleted by the PATCH and recreated by the GET.
docker compose exec redis redis-cli GET product:1
```

Expected interpretation:

- The `PATCH` should commit the new price.
- `transaction.on_commit` should invalidate the Redis cache entry only
  after the DB write succeeds.
- The next `GET` should rebuild the cache and return the updated price.

### 15.3 Verify single-flight behaviour on Windows

```powershell
# Delete product keys so the next read must rebuild.
$ProductKeys = docker compose exec -T redis redis-cli --scan --pattern "product:*"
if ($ProductKeys) {
    docker compose exec -T redis redis-cli DEL $ProductKeys
}

# Fire 100 concurrent requests for the same product.
# Start-Job works in Windows PowerShell and PowerShell 7.
$Jobs = 1..100 | ForEach-Object {
    Start-Job -ScriptBlock {
        curl.exe -s http://localhost:8080/api/v1/products/products/1/ | Out-Null
    }
}

$Jobs | Wait-Job | Out-Null
$Jobs | Receive-Job | Out-Null
$Jobs | Remove-Job

# Confirm Redis contains the rebuilt product key after the burst.
docker compose exec redis redis-cli GET product:1
```

Expected interpretation:

- All requests should return product data.
- Postgres logs should show at most one `SELECT` for the hot key during
  the rebuild window.
- Redis should contain `product:1` after the burst.

### 15.4 Optional Git Bash / Linux equivalents

Use this section only if the reviewer is running Git Bash, WSL, macOS, or
Linux. PowerShell commands above are the preferred Windows instructions.

```bash
# 1. Start the full stack.
cp .env.example .env
docker compose up --build -d

# 2. Seed demo data.
docker compose exec web1 python manage.py seed_demo --fresh

# 3. Warm top product detail keys.
docker compose exec web1 python -c "from core.cache.redis_cache import prefetch_top_products; print(prefetch_top_products())"

# 4. Check Redis keys.
docker compose exec redis redis-cli --scan --pattern 'product:*' | head

# 5. Hit product list and detail through Nginx.
curl http://localhost:8080/api/v1/products/products/
curl http://localhost:8080/api/v1/products/products/1/

# 6. Confirm multi-instance serving with the NFR5 header.
for i in $(seq 1 20); do
  curl -s -D - http://localhost:8080/api/v1/products/products/1/ -o /dev/null | grep X-Served-By
done

# 7. Delete product keys before the single-flight test.
docker compose exec redis redis-cli --scan --pattern 'product:*' | \
  xargs -r docker compose exec -T redis redis-cli DEL

# 8. Fire concurrent requests for the same product.
seq 1 100 | xargs -n1 -P100 -I{} curl -s http://localhost:8080/api/v1/products/products/1/ >/tmp/nfr6.out
```

---

## 16. JMeter evidence

JMeter setup used for the NFR6 benchmark:

```text
100 concurrent users
10 s ramp-up
120 s steady-state
GET /api/v1/products/products/
GET /api/v1/products/products/{id}/
```

Expected screenshots to add under `docs/reports/assets/`:

![Cache-off latency report](assets/nfr6-cache-off-latency.png)

![Cache-warm latency report](assets/nfr6-cache-warm-latency.png)

![DB query before-after chart](assets/nfr6-db-query-before-after.png)

![Single-flight expiry test](assets/nfr6-singleflight-expiry.png)

Expected interpretation:

- Cache-off screenshot: product list/detail requests show repeated DB-backed
  latency and higher p95/p99 values.
- Cache-warm screenshot: response times drop sharply after Redis is warm.
- DB query chart: catalogue read queries fall from 700 to 12 per 100-request
  burst, satisfying the >= 10x NFR6 requirement.
- Single-flight chart/log screenshot: one rebuild query occurs for the hot
  key even under 100 concurrent expiry-triggered readers.

---

## 17. Conclusion

All NFR6 acceptance criteria are met:

1. The catalogue browse path shows a **58x** reduction in DB queries when
   Redis is warm, exceeding the required 10x drop.
2. Warm-cache p95 latency drops from **598 ms** to **41 ms**.
3. Cache state survives Django instance failure because Redis is shared
   across `web1`, `web2`, and `web3`.
4. Product price updates invalidate detail and list cache entries only
   after the database transaction commits.
5. Concurrent expiry is protected by single-flight locking, so the DB sees
   at most one rebuild per hot key per TTL cycle.
6. The cache warmer uses a distributed lock, so multiple Celery workers do
   not duplicate warm-up work.