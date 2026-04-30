# Postman collection

Two files:

- `ecommerce_engine.postman_collection.json` — full request set, organised
  into eight folders (Health, Auth, Addresses, Catalog, Cart, Orders,
  Inventory, Payments).
- `ecommerce_engine.postman_environment.json` — environment with
  `base_url`, seeded credentials, and ID placeholders.

## Usage

1. Bring the stack up: `docker-compose up --build`.
2. Seed data:
   ```
   docker-compose exec web1 python manage.py seed_demo --fresh
   ```
3. In Postman, **Import** both JSON files. Pick the environment
   `Local — docker-compose`.
4. Run **Auth → Token Login**. The test script captures the returned
   token into `{{auth_token}}` automatically; every other request inherits
   bearer auth from the collection.

## Conventions

- Collection-level pre-request script applies the `Authorization: Token <key>`
  header from the saved variable, matching DRF's `TokenAuthentication`.
- Endpoints that should be anonymous (catalog browse, register, webhook)
  override `auth` to `noauth`.
- Request bodies use placeholders (`{{product_id}}`, `{{order_id}}`, ...).
  After running a `place order` or `add cart item`, the test script
  captures the new ID into the right variable so the next call works
  without manual edits.

## Demo flow (anchored to NFRs)

The collection is ordered so a top-to-bottom run exercises the headline
NFR scenarios:

| Step | Folder | NFR exercised |
|---|---|---|
| Healthz | 0 | NFR5 (Nginx round-trip + `X-Served-By` header) |
| Token login | 1 | baseline auth |
| Browse + product detail | 3 | NFR6 (cache hit on second call) |
| Add to cart + place order | 4–5 | NFR1 (race-free reservation), NFR8 (atomicity), NFR3 (async dispatch) |
| Inventory list / stock movements | 6 | NFR1 (audit trail correctness) |
| Capture payment | 7 | NFR1 + NFR8 (composite write) |
| Webhook fired twice with same signature | 7 | NFR1 (idempotency on `external_id`) |

Headers worth inspecting in any response:

- `X-Instance-Id` — which Django instance served the request (NFR5).
- `X-Response-Time-ms` — middleware-measured latency (AOP, NFR10).
- `X-Served-By` — upstream from Nginx (NFR5).
