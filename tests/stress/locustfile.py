"""
Stress test scenario - [NFR9].

Goal: prove the system serves >= 100 concurrent users without crashes or
data loss. The locust UI is reachable on http://localhost:8089 once the
stack is up.

Scenarios (filled in by the NFR9 owner):

  - BrowseOnly        : list/detail reads, no writes. Stresses NFR6 cache.
  - CheckoutFlow      : login -> add to cart -> place order -> capture.
                         Stresses NFR1, NFR7, NFR8.
  - WebhookStorm      : hammers /payments/webhook/ to test idempotency.
  - Mixed             : 80 % browse + 20 % checkout (the KPI scenario).

The NFR9 deliverable is a markdown report under
docs/benchmarks/stress-<date>.md with:
  - hardware profile,
  - p50 / p95 / p99,
  - failure count,
  - DB / Redis / Celery saturation graphs.
"""
from locust import HttpUser, between, task


class BrowseOnly(HttpUser):
    """Read-heavy traffic - exercises the cache layer (NFR6)."""

    wait_time = between(0.5, 2.0)

    @task(5)
    def list_products(self):
        # TODO [NFR9]: parameterize ?category= for realistic distribution.
        self.client.get("/api/v1/products/products/")

    @task(2)
    def detail(self):
        # TODO [NFR9]: pick a product id from a pre-seeded list.
        self.client.get("/api/v1/products/products/1/")


class CheckoutFlow(HttpUser):
    """Full purchase - exercises NFR1, NFR7, NFR8 hot paths."""

    wait_time = between(1.0, 3.0)

    def on_start(self):
        # TODO [NFR9]: log in or register a fresh user, store the auth
        #              token / session cookie on self.
        pass

    @task
    def buy(self):
        # TODO [NFR9]: add to cart, place order, capture payment.
        pass


class WebhookStorm(HttpUser):
    """Idempotency stress - duplicate webhooks for the same intent."""

    wait_time = between(0.1, 0.5)

    @task
    def replay(self):
        # TODO [NFR9]: post the same signature repeatedly to /webhook/.
        pass
