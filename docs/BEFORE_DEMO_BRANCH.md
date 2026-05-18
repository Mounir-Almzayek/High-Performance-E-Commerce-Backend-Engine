# Before-Demo Branch

This branch is intentionally unsafe and exists only to capture "before"
evidence for the course report.

Do not merge it into `main`.

Disabled or weakened behavior:

- NFR1: inventory/cart row locks are removed and a small delay is added
  to make race conditions visible under JMeter/Locust.
- NFR2: service-level capacity decorators and bounded executors do not
  enforce limits.
- NFR3: invoice and notification tasks are executed synchronously inside
  checkout, increasing user-visible latency.
- NFR4: batch processing materializes the whole queryset and processes it
  as one large batch instead of chunking.
- NFR5: Nginx forwards all requests to `web1`, so there is no meaningful
  load distribution.

Use this branch to capture before screenshots, then switch back to
`main` and run the exact same scenarios for after screenshots.
