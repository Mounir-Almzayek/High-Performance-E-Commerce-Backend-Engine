# JMeter Test Plans

This folder contains the JMeter artifacts expected in the submission.

## Files

| File | Purpose |
|---|---|
| `race-condition-checkout.jmx` | Sends many simultaneous checkout requests to the same prepared cart/order flow to prove the race before/after fix |
| `resource-management-products.jmx` | Generates read/search traffic for NFR2 resource-management comparison |
| `async-payment-capture.jmx` | Exercises payment capture so NFR3 timing can be compared with Celery/Flower screenshots |
| `load-distribution-nfr5.jmx` | Sends repeated requests through Nginx and captures backend distribution / failover evidence |

## Before running

Start the stack and seed data:

```bash
docker-compose up --build
docker-compose exec web1 python manage.py seed_demo --fresh
```

Log in with Postman or curl and collect:

- `AUTH_TOKEN`
- `SHIPPING_ADDRESS_ID`
- `BILLING_ADDRESS_ID`
- product IDs for cart setup
- payment intent ID for the async/payment capture plan

The JMX files are parameterized. In JMeter, set the variables in the
`User Defined Variables` element before running.

## Required screenshots

Save screenshots in:

```text
docs/reports/assets/
```

Use these exact names so the reports render automatically:

| Screenshot | Where it is referenced |
|---|---|
| `race-before-jmeter.png` | NFR1 report |
| `race-after-jmeter.png` | NFR1 report |
| `resource-low-workers-jmeter.png` | NFR2 report |
| `resource-balanced-workers-jmeter.png` | NFR2 report |
| `resource-monitoring-before.png` | NFR2 report |
| `resource-monitoring-after.png` | NFR2 report |
| `async-checkout-before.png` | NFR3 report |
| `async-checkout-after.png` | NFR3 report |
| `async-flower-retry.png` | NFR3 report |
| `nfr5-roundrobin-histogram.png` | NFR5 report |
| `nfr5-leastconn-histogram.png` | NFR5 report |
| `nfr5-failover-recovery.png` | NFR5 report |
| `nfr9-100-users-locust.png` | NFR9/full-system evidence |

## Exported result files

JMeter screenshots are required, but also save raw results when possible:

```text
tools/jmeter/results/
```

Suggested names:

- `race-before.jtl`
- `race-after.jtl`
- `resource-low-workers.jtl`
- `resource-balanced-workers.jtl`
- `async-payment-capture.jtl`
- `load-distribution-nfr5.jtl`

