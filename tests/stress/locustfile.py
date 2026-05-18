"""
Stress / load test scenarios — NFR1, NFR2, NFR5, NFR9.

How to run (from project root, with docker-compose up):
    locust -f tests/stress/locustfile.py --host http://localhost

Scenarios:
  BrowseOnly     : read-only traffic  (cache / NFR6)
  CheckoutFlow   : full purchase path (NFR1 race condition, NFR8 ACID)
  WebhookStorm   : duplicate webhooks (NFR3 idempotency)
  ResourceStress : overload checkout  (NFR2 pool / 503 behavior)

Recommended KPI run (Mixed weights already set):
    locust -f tests/stress/locustfile.py \
           --host http://localhost \
           --users 100 --spawn-rate 10 \
           --run-time 60s --headless \
           --html results/nfr1_stress.html
"""
from __future__ import annotations

import random
import uuid

from locust import HttpUser, between, task

BASE = "/api/v1"

# Assume seed data creates products with IDs 1..10.
# Adjust if your fixtures use different IDs.
PRODUCT_IDS = list(range(1, 11))


# ─────────────────────────────────────────────────────────────────────────────
# BROWSE ONLY — read-heavy, exercises cache layer (NFR6)
# ─────────────────────────────────────────────────────────────────────────────

class BrowseOnly(HttpUser):
    """
    80 % of users in the Mixed scenario.
    Exercises: product listing, product detail, inventory check.
    Stresses: NFR6 distributed cache hit rate.
    """

    weight = 4
    wait_time = between(0.5, 2.0)

    @task(5)
    def list_products(self):
        self.client.get(
            f"{BASE}/products/products/",
            name="/products/products/ [list]",
        )

    @task(3)
    def product_detail(self):
        pid = random.choice(PRODUCT_IDS)
        self.client.get(
            f"{BASE}/products/products/{pid}/",
            name="/products/products/[id]/",
        )

    @task(1)
    def cart_peek(self):
        """Authenticated browse: check current cart state."""
        if getattr(self, "token", None):
            self.client.get(
                f"{BASE}/cart/",
                headers=self._auth(),
                name="/cart/ [peek]",
            )

    # Minimal auth so browsing users can also check their cart.
    def on_start(self):
        self.token = None
        suffix = uuid.uuid4().hex[:8]
        self._username = f"browse_{suffix}"
        resp = self.client.post(
            f"{BASE}/users/register/",
            json={
                "username": self._username,
                "email": f"{self._username}@test.com",
                "password": "BrowsePass1!",
            },
            name="/users/register/ [browse]",
        )
        if resp.status_code != 201:
            return
        resp = self.client.post(
            f"{BASE}/users/token/",
            json={"username": self._username, "password": "BrowsePass1!"},
            name="/users/token/ [browse]",
        )
        if resp.status_code == 200:
            self.token = resp.json().get("token")

    def _auth(self):
        return {"Authorization": f"Token {self.token}"}


# ─────────────────────────────────────────────────────────────────────────────
# CHECKOUT FLOW — full purchase: register → cart → order → capture
# ─────────────────────────────────────────────────────────────────────────────

class CheckoutFlow(HttpUser):
    """
    20 % of users in the Mixed scenario.
    Exercises: NFR1 race condition on inventory, NFR7 concurrency control,
               NFR8 ACID transactions, NFR3 async task dispatch.

    NFR1 proof: when many CheckoutFlow users compete for the SAME product
    (product_id=1 with limited stock), only as many succeed as units exist.
    The rest receive 400/409. Run with --users 50 against a product seeded
    with 10 units to observe exactly 10 successes.
    """

    weight = 1
    wait_time = between(1.0, 3.0)

    token: str | None = None
    address_id: int | None = None

    def on_start(self):
        """Register a unique user, get a token, create an address."""
        suffix = uuid.uuid4().hex[:10]
        self._username = f"checkout_{suffix}"
        password = "CheckPass1!"

        # ── 1. Register ───────────────────────────────────────────────────
        resp = self.client.post(
            f"{BASE}/users/register/",
            json={
                "username": self._username,
                "email": f"{self._username}@test.com",
                "password": password,
            },
            name="/users/register/ [checkout]",
        )
        if resp.status_code != 201:
            return

        # ── 2. Token ──────────────────────────────────────────────────────
        resp = self.client.post(
            f"{BASE}/users/token/",
            json={"username": self._username, "password": password},
            name="/users/token/ [checkout]",
        )
        if resp.status_code != 200:
            return
        self.token = resp.json().get("token")

        # ── 3. Create address (both shipping & billing) ───────────────────
        addr_payload = {
            "kind": "shipping",
            "line1": "1 Load Test Ave",
            "city": "Testville",
            "region": "TX",
            "postal_code": "75001",
            "country": "US",
            "is_default": True,
        }
        resp = self.client.post(
            f"{BASE}/users/addresses/",
            json=addr_payload,
            headers=self._auth(),
            name="/users/addresses/ [create]",
        )
        if resp.status_code == 201:
            self.address_id = resp.json().get("id")

    def _auth(self):
        return {"Authorization": f"Token {self.token}"}

    @task
    def buy(self):
        """
        One full checkout cycle:
        clear cart → add item → place order → create intent → capture.

        Uses product_id=1 deliberately to maximise contention on a single
        hot StockItem row (demonstrates NFR1 SELECT FOR UPDATE in action).
        Vary PRODUCT_IDS weights here if you want a broader contention test.
        """
        if not self.token or not self.address_id:
            return

        # ── 1. Clear stale cart ───────────────────────────────────────────
        self.client.post(
            f"{BASE}/cart/clear/",
            headers=self._auth(),
            name="/cart/clear/",
        )

        # ── 2. Add one hot item ───────────────────────────────────────────
        # Using product 1 to force maximum lock contention (NFR1 demo).
        hot_pid = random.choice([1, 1, 1, 2, 3])  # weight 1 most of the time
        resp = self.client.post(
            f"{BASE}/cart/items/",
            json={"product_id": hot_pid, "quantity": 1},
            headers=self._auth(),
            name="/cart/items/ [add]",
        )
        if resp.status_code != 201:
            return

        # ── 3. Place order ────────────────────────────────────────────────
        resp = self.client.post(
            f"{BASE}/orders/place/",
            json={
                "shipping_address_id": self.address_id,
                "billing_address_id": self.address_id,
            },
            headers=self._auth(),
            name="/orders/place/",
        )
        if resp.status_code != 201:
            # 400 = not enough stock (NFR1 lock worked correctly)
            return
        order_id = resp.json().get("id")

        # ── 4. Create payment intent ──────────────────────────────────────
        resp = self.client.post(
            f"{BASE}/payments/intents/",
            json={"order_id": order_id},
            headers=self._auth(),
            name="/payments/intents/ [create]",
        )
        if resp.status_code != 201:
            return
        intent_id = resp.json().get("id")

        # ── 5. Capture ────────────────────────────────────────────────────
        external_id = f"ext_{uuid.uuid4().hex}"
        self.client.post(
            f"{BASE}/payments/intents/{intent_id}/capture/",
            json={"external_id": external_id},
            headers=self._auth(),
            name="/payments/intents/[id]/capture/",
        )


# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK STORM — duplicate webhooks, tests NFR3 idempotency
# ─────────────────────────────────────────────────────────────────────────────

class WebhookStorm(HttpUser):
    """
    Replays a small pool of webhook signatures at high frequency.

    NFR3 proof: every replay of a known signature must return 204 without
    creating a duplicate WebhookEvent row. Run:
        locust -f tests/stress/locustfile.py \
               --host http://localhost \
               --users 20 --spawn-rate 20 \
               --run-time 30s --headless \
               -T WebhookStorm

    Expected: 0 failures, 100 % 204 responses.
    """

    weight = 0          # not included in Mixed by default; run explicitly
    wait_time = between(0.05, 0.3)

    # Fixed pool of 5 signatures — every user replays from the same pool.
    # This maximises the DB UNIQUE constraint hit rate.
    _SIG_POOL = [f"gw_sig_{uuid.uuid4().hex}" for _ in range(5)]

    @task
    def replay(self):
        sig = random.choice(self._SIG_POOL)
        self.client.post(
            f"{BASE}/payments/webhook/",
            json={"event": "payment.captured", "amount": "49.99", "currency": "USD"},
            headers={"X-Gateway-Signature": sig},
            name="/payments/webhook/ [replay]",
        )


# ─────────────────────────────────────────────────────────────────────────────
# RESOURCE STRESS — overloads checkout pool to verify 503 behavior (NFR2)
# ─────────────────────────────────────────────────────────────────────────────

class ResourceStress(HttpUser):
    """
    Fires concurrent checkout + capture requests to saturate the
    capacity_limited("checkout") and capacity_limited("payment") pools.

    NFR2 proof: when in_flight >= limit, the system returns HTTP 503
    immediately (no crash, no hang). The 503 rate should spike then drop
    as VUs back off; the server stays responsive for the remaining requests.

    Run:
        locust -f tests/stress/locustfile.py \
               --host http://localhost \
               --users 30 --spawn-rate 30 \
               --run-time 30s --headless \
               -T ResourceStress

    Look for 503 responses in the Locust report — that is the expected
    graceful degradation. 0 × 5xx other than 503 = pool is working.
    """

    weight = 0          # not included in Mixed; run explicitly
    wait_time = between(0.01, 0.1)

    token: str | None = None
    address_id: int | None = None

    def on_start(self):
        suffix = uuid.uuid4().hex[:8]
        username = f"stress_{suffix}"
        password = "StressPass1!"

        resp = self.client.post(
            f"{BASE}/users/register/",
            json={"username": username, "email": f"{username}@test.com", "password": password},
            name="/users/register/ [stress]",
        )
        if resp.status_code != 201:
            return

        resp = self.client.post(
            f"{BASE}/users/token/",
            json={"username": username, "password": password},
            name="/users/token/ [stress]",
        )
        if resp.status_code != 200:
            return
        self.token = resp.json().get("token")

        resp = self.client.post(
            f"{BASE}/users/addresses/",
            json={
                "kind": "shipping",
                "line1": "99 Stress Blvd",
                "city": "Stresstown",
                "region": "CA",
                "postal_code": "90001",
                "country": "US",
                "is_default": True,
            },
            headers={"Authorization": f"Token {self.token}"},
            name="/users/addresses/ [stress]",
        )
        if resp.status_code == 201:
            self.address_id = resp.json().get("id")

    @task
    def hammer_checkout(self):
        if not self.token or not self.address_id:
            return
        auth = {"Authorization": f"Token {self.token}"}

        self.client.post(f"{BASE}/cart/clear/", headers=auth, name="/cart/clear/ [stress]")

        resp = self.client.post(
            f"{BASE}/cart/items/",
            json={"product_id": random.choice(PRODUCT_IDS), "quantity": 1},
            headers=auth,
            name="/cart/items/ [stress]",
        )
        if resp.status_code != 201:
            return

        resp = self.client.post(
            f"{BASE}/orders/place/",
            json={
                "shipping_address_id": self.address_id,
                "billing_address_id": self.address_id,
            },
            headers=auth,
            name="/orders/place/ [stress]",
        )
        if resp.status_code != 201:
            # 400 = out of stock OR 503 = pool full — both are expected
            return
        order_id = resp.json().get("id")

        resp = self.client.post(
            f"{BASE}/payments/intents/",
            json={"order_id": order_id},
            headers=auth,
            name="/payments/intents/ [stress]",
        )
        if resp.status_code != 201:
            return
        intent_id = resp.json().get("id")

        self.client.post(
            f"{BASE}/payments/intents/{intent_id}/capture/",
            json={"external_id": f"ext_{uuid.uuid4().hex}"},
            headers=auth,
            name="/payments/intents/[id]/capture/ [stress]",
        )
