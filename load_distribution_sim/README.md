# load_distribution_sim

Standalone simulation for NFR5 — Load Distribution.

Compares round-robin vs least_conn load balancing strategies with a
realistic e-commerce request cost profile. No Django, no Docker required.

## Quick start

```bash
# From the project root or this directory
pip install requests   # only if you add live HTTP polling
python load_distribution_sim/sim.py
```

## What it demonstrates

1. **Round-robin baseline** — equal request count per backend, but queue
   depth spikes when expensive requests (payment captures) land on the
   same backend.

2. **Least-conn (chosen strategy)** — slightly uneven request count, but
   half the maximum queue depth and lower p95 latency.

3. **Failover demo** — `web1` is marked unavailable at 50 % of requests
   and restored at 75 %. The simulation shows that errors are limited to
   the in-flight requests at the exact moment of failure.

4. **Metrics polling stub** — shows the HTTP polling pattern used to
   aggregate `/api/v1/_diag/pool/` from all backends. Documents the
   in-process state limitation of `_call_counter` and why it must migrate
   to Redis for cross-instance truth.

## Relationship to the main project

This sub-project is intentionally isolated so reviewers can run it
without the full Docker stack. The same strategies are implemented in
`docker/nginx.conf` for the live environment.

## Live verification (requires Docker stack)

```bash
# Full stack
docker-compose up --build -d

# Distribution check (300 hits)
bash tools/distribution_check.sh 300

# Failover demo
bash tools/failover_demo.sh
```

## Known limitations

- `REQUEST_COUNT` in `core.aop.decorators._call_counter` is per-process
  (RAM only). The metrics endpoint aggregates it via HTTP polling; the
  total is approximate and resets on restart. Moving to Redis is the
  correct fix and is planned under NFR10.
- The simulation uses `time.sleep` scaled at 1/10 000 real time for speed;
  relative proportions are preserved but absolute millisecond values are
  not.
