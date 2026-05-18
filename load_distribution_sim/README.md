# Load Distribution Simulation

Small standalone simulation for NFR5. It compares round-robin with
least-connection routing across three backend instances using a mixed
e-commerce workload.

Run from the repository root:

```bash
python load_distribution_sim/sim.py
```

What to look for:

- Round-robin usually gives nearly equal request counts.
- Least-connection usually gives lower max queue depth when expensive
  checkout/payment requests are mixed with cheap product-list requests.

This supports the engineering choice in `docker/nginx.conf`: request
count equality is less important than keeping backend queues balanced
under uneven request cost.
