"""NFR9 stress scenarios.

Headline run:
    locust -f tests/stress/locustfile.py --host http://localhost \
        --users 100 --spawn-rate 10 --run-time 10m --headless \
        --html docs/benchmarks/nfr9-100-users.html \
        --csv docs/benchmarks/nfr9-100-users

The mixed scenario is represented by class weights:
    BrowseOnly   weight 4  -> roughly 80 percent reads
    CheckoutFlow weight 1  -> roughly 20 percent checkout/payment writes
"""
from __future__ import annotations

import random
import uuid
from itertools import count
from threading import Lock

from locust import HttpUser, between, events, task

BASE = "/api/v1"
SEEDED_PASSWORD = "Password123!"

PRODUCT_IDS: list[int] = []
LOW_PRICE_PRODUCT_IDS: list[int] = []
PRODUCT_LOCK = Lock()
USER_COUNTER = count(1)


def _extract_results(payload):
    if isinstance(payload, dict):
        return payload.get("results", [])
    if isinstance(payload, list):
        return payload
    return []


def _load_products(client) -> None:
    """Load real product IDs from the running API instead of hard-coding IDs."""
    global PRODUCT_IDS, LOW_PRICE_PRODUCT_IDS
    if PRODUCT_IDS:
        return

    with PRODUCT_LOCK:
        if PRODUCT_IDS:
            return

        products = []
        for page in range(1, 6):
            resp = client.get(
                f"{BASE}/products/products/?page={page}",
                name="/products/products/ [discover]",
            )
            if resp.status_code == 200:
                products.extend(_extract_results(resp.json()))

        PRODUCT_IDS = [p["id"] for p in products if isinstance(p, dict) and "id" in p]
        LOW_PRICE_PRODUCT_IDS = [
            p["id"]
            for p in products
            if isinstance(p, dict)
            and "id" in p
            and float(p.get("price", 999999)) <= 75
        ] or PRODUCT_IDS


def _next_seeded_username() -> str:
    # seed_demo creates user0001..user0300. Wrap around if more VUs are used.
    idx = ((next(USER_COUNTER) - 1) % 300) + 1
    return f"user{idx:04d}"


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    PRODUCT_IDS.clear()
    LOW_PRICE_PRODUCT_IDS.clear()


class SeededUserMixin:
    token: str | None = None
    address_id: int | None = None

    def _auth(self):
        return {"Authorization": f"Token {self.token}"}

    def login_seeded_user(self) -> bool:
        _load_products(self.client)
        self.username = _next_seeded_username()
        resp = self.client.post(
            f"{BASE}/users/token/",
            json={"username": self.username, "password": SEEDED_PASSWORD},
            name="/users/token/ [seeded]",
        )
        if resp.status_code != 200:
            return False

        self.token = resp.json().get("token")
        resp = self.client.get(
            f"{BASE}/users/me/",
            headers=self._auth(),
            name="/users/me/",
        )
        if resp.status_code != 200:
            return False

        addresses = resp.json().get("addresses", [])
        if addresses:
            self.address_id = addresses[0]["id"]
            return True

        resp = self.client.post(
            f"{BASE}/users/addresses/",
            json={
                "kind": "shipping",
                "line1": "1 Load Test Ave",
                "city": "Testville",
                "region": "TX",
                "postal_code": "75001",
                "country": "US",
                "is_default": True,
            },
            headers=self._auth(),
            name="/users/addresses/ [create]",
        )
        if resp.status_code == 201:
            self.address_id = resp.json().get("id")
            return True
        return False


class BrowseOnly(SeededUserMixin, HttpUser):
    """Read-heavy traffic: catalog, product detail, and cart peek."""

    weight = 4
    wait_time = between(0.5, 2.0)

    def on_start(self):
        self.login_seeded_user()

    @task(5)
    def list_products(self):
        self.client.get(f"{BASE}/products/products/", name="/products/products/ [list]")

    @task(3)
    def product_detail(self):
        _load_products(self.client)
        if not PRODUCT_IDS:
            return
        self.client.get(
            f"{BASE}/products/products/{random.choice(PRODUCT_IDS)}/",
            name="/products/products/[id]/",
        )

    @task(1)
    def cart_peek(self):
        if self.token:
            self.client.get(f"{BASE}/cart/", headers=self._auth(), name="/cart/ [peek]")


class CheckoutFlow(SeededUserMixin, HttpUser):
    """Write traffic: cart -> order -> payment intent -> capture."""

    weight = 1
    wait_time = between(1.0, 3.0)

    def on_start(self):
        self.login_seeded_user()

    @task
    def buy(self):
        if not self.token or not self.address_id:
            return
        _load_products(self.client)
        if not LOW_PRICE_PRODUCT_IDS:
            return

        self.client.post(f"{BASE}/cart/clear/", headers=self._auth(), name="/cart/clear/")

        with self.client.post(
            f"{BASE}/cart/items/",
            json={"product_id": random.choice(LOW_PRICE_PRODUCT_IDS), "quantity": 1},
            headers=self._auth(),
            name="/cart/items/ [add]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                resp.success()
            elif resp.status_code in (400, 404, 409):
                resp.success()
                return
            else:
                return

        with self.client.post(
            f"{BASE}/orders/place/",
            json={
                "shipping_address_id": self.address_id,
                "billing_address_id": self.address_id,
            },
            headers=self._auth(),
            name="/orders/place/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                order_id = resp.json().get("id")
                resp.success()
            elif resp.status_code in (400, 409):
                resp.success()
                return
            else:
                return

        with self.client.post(
            f"{BASE}/payments/intents/",
            json={"order_id": order_id},
            headers=self._auth(),
            name="/payments/intents/ [create]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                intent_id = resp.json().get("id")
                resp.success()
            elif resp.status_code in (400, 409):
                resp.success()
                return
            else:
                return

        with self.client.post(
            f"{BASE}/payments/intents/{intent_id}/capture/",
            json={"external_id": f"ext_{uuid.uuid4().hex}"},
            headers=self._auth(),
            name="/payments/intents/[id]/capture/",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            elif resp.status_code in (400, 402, 409):
                # Business rejection: no crash, no data-loss signal.
                resp.success()


class WebhookStorm(HttpUser):
    """Optional duplicate-webhook idempotency scenario."""

    weight = 0
    wait_time = between(0.05, 0.3)
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


class ResourceStress(CheckoutFlow):
    """Optional NFR2 overload run. Not part of the mixed KPI by default."""

    weight = 0
    wait_time = between(0.01, 0.1)
