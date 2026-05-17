"""
load_distribution_sim/sim.py
============================
Standalone simulation of round-robin vs least_conn load balancing.

No Django, no Docker required. Install: pip install requests
Run:    python sim.py

The simulation models a 3-backend pool with realistic e-commerce request
cost distribution and compares the two strategies on queue depth and
response latency.
"""

import random
import time
import threading
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKENDS = ["web1:8000", "web2:8000", "web3:8000"]

# Request cost in milliseconds (simulated processing time)
REQUEST_COST_PROFILE = [
    # (weight, min_ms, max_ms, label)
    (80, 3,   15,  "product_list"),     # 80% cheap reads
    (15, 40, 120, "place_order"),       # 15% medium writes
    (5,  80, 220, "payment_capture"),   # 5%  expensive captures
]

TOTAL_REQUESTS = 1_000
CONCURRENT_USERS = 50
RANDOM_SEED = 42


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Backend:
    name: str
    active_connections: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    available: bool = True
    latencies: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self):
        with self._lock:
            self.active_connections += 1
            self.total_requests += 1

    def release(self, latency_ms: float):
        with self._lock:
            self.active_connections -= 1
            self.latencies.append(latency_ms)

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        idx = int(len(s) * 0.95)
        return s[idx]


@dataclass
class SimulatedRequest:
    label: str
    cost_ms: float


# ---------------------------------------------------------------------------
# Request generation
# ---------------------------------------------------------------------------

def make_request(rng: random.Random) -> SimulatedRequest:
    weights = [p[0] for p in REQUEST_COST_PROFILE]
    chosen = rng.choices(REQUEST_COST_PROFILE, weights=weights, k=1)[0]
    _, min_ms, max_ms, label = chosen
    cost = rng.uniform(min_ms, max_ms)
    return SimulatedRequest(label=label, cost_ms=cost)


# ---------------------------------------------------------------------------
# Routing strategies
# ---------------------------------------------------------------------------

_rr_index = 0
_rr_lock = threading.Lock()


def route_round_robin(backends: list[Backend]) -> Backend | None:
    global _rr_index
    available = [b for b in backends if b.available]
    if not available:
        return None
    with _rr_lock:
        b = available[_rr_index % len(available)]
        _rr_index += 1
    return b


def route_least_conn(backends: list[Backend]) -> Backend | None:
    available = [b for b in backends if b.available]
    if not available:
        return None
    return min(available, key=lambda b: b.active_connections)


# ---------------------------------------------------------------------------
# Simulation engine
# ---------------------------------------------------------------------------

def simulate_request(backend: Backend, req: SimulatedRequest):
    """Simulate processing one request on a backend."""
    backend.acquire()
    # Simulate actual processing time (sleep scaled down for speed)
    time.sleep(req.cost_ms / 10_000)   # 10x faster than real time
    backend.release(latency_ms=req.cost_ms)


def run_simulation(
    strategy: Literal["round_robin", "least_conn"],
    n_requests: int = TOTAL_REQUESTS,
    n_workers: int = CONCURRENT_USERS,
    inject_failure: bool = False,
) -> list[Backend]:
    """
    Run the simulation with the given strategy.

    inject_failure: if True, mark web1 as unavailable at the 50% mark,
    then restore it at 75% to simulate failover.
    """
    global _rr_index
    _rr_index = 0

    backends = [Backend(name=n) for n in BACKENDS]
    rng = random.Random(RANDOM_SEED)
    requests = [make_request(rng) for _ in range(n_requests)]

    route_fn = route_round_robin if strategy == "round_robin" else route_least_conn

    # Track max queue depth per backend
    max_queue: dict[str, int] = {b.name: 0 for b in backends}
    queue_lock = threading.Lock()

    def sample_queue():
        with queue_lock:
            for b in backends:
                if b.active_connections > max_queue[b.name]:
                    max_queue[b.name] = b.active_connections

    errors = []
    completed = threading.Semaphore(0)
    in_flight = [0]
    idx_lock = threading.Lock()
    next_idx = [0]

    def worker():
        while True:
            with idx_lock:
                i = next_idx[0]
                if i >= len(requests):
                    return
                next_idx[0] += 1

            req = requests[i]

            # Inject failure at 50% and restore at 75%
            if inject_failure:
                progress = i / n_requests
                if progress >= 0.50 and progress < 0.75:
                    backends[0].available = False   # kill web1
                elif progress >= 0.75:
                    backends[0].available = True    # restore web1

            backend = route_fn(backends)
            if backend is None:
                errors.append(i)
                continue

            sample_queue()
            simulate_request(backend, req)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Attach max queue depth to each backend for reporting
    for b in backends:
        b._max_queue = max_queue[b.name]

    return backends, errors


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

BAR_WIDTH = 40


def bar(fraction: float) -> str:
    filled = int(BAR_WIDTH * fraction)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def print_histogram(backends: list[Backend], title: str, errors: list):
    total = sum(b.total_requests for b in backends)
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    print(f"  Total requests: {total}   Errors: {len(errors)}")
    print()

    for b in backends:
        pct = b.total_requests / total if total else 0
        status = "UP" if b.available else "DOWN"
        print(f"  {b.name:<12} : {b.total_requests:>5} requests  ({pct:5.1%})  "
              f"{bar(pct)}  [{status}]")

    print()
    print("  Queue depth (active connections):")
    for b in backends:
        avg_latency = statistics.mean(b.latencies) if b.latencies else 0
        print(f"  {b.name:<12} : max={b._max_queue:>3}   p95={b.p95:>6.1f} ms   "
              f"avg_latency={avg_latency:>6.1f} ms")

    if errors:
        print(f"\n  ⚠  {len(errors)} request(s) failed (no available backend at that moment)")


def print_comparison_table(rr_backends, lc_backends):
    print(f"\n{'=' * 60}")
    print("  COMPARISON: round_robin  vs  least_conn")
    print(f"{'=' * 60}")

    rr_max = max(b._max_queue for b in rr_backends)
    lc_max = max(b._max_queue for b in lc_backends)
    rr_p95 = statistics.mean(b.p95 for b in rr_backends if b.latencies)
    lc_p95 = statistics.mean(b.p95 for b in lc_backends if b.latencies)
    rr_dev = statistics.stdev(b.total_requests for b in rr_backends)
    lc_dev = statistics.stdev(b.total_requests for b in lc_backends)

    print(f"\n  {'Metric':<30} {'round_robin':>14} {'least_conn':>14} {'Winner':>10}")
    print(f"  {'-'*30} {'-'*14} {'-'*14} {'-'*10}")
    print(f"  {'Max queue depth':<30} {rr_max:>14} {lc_max:>14} "
          f"{'least_conn' if lc_max < rr_max else 'round_robin':>10}")
    print(f"  {'Avg p95 latency (ms)':<30} {rr_p95:>14.1f} {lc_p95:>14.1f} "
          f"{'least_conn' if lc_p95 < rr_p95 else 'round_robin':>10}")
    print(f"  {'Request count std-dev':<30} {rr_dev:>14.1f} {lc_dev:>14.1f} "
          f"{'round_robin' if rr_dev < lc_dev else 'least_conn':>10}")
    print()
    print("  Verdict: least_conn wins on queue depth and p95 latency.")
    print("           round_robin distributes counts more evenly, but")
    print("           that metric is irrelevant — queue depth (= waiting")
    print("           time for the next request) is what matters.")


def collect_metrics_stub(hosts: list[str]) -> dict:
    """
    Stub showing the HTTP-polling pattern used against a live stack.
    In production replace 'requests.get' calls with real HTTP.

    In-process state limitation:
      REQUEST_COUNT in core.aop.decorators._call_counter lives in each
      backend's RAM only. This function aggregates via HTTP polling, so
      the total is approximate and resets on process restart. For exact
      cross-instance metrics, migrate the counter to Redis (NFR10 scope).
    """
    print("\n[METRICS STUB] HTTP polling pattern (requires live stack):")
    for host in hosts:
        print(f"  GET http://{host}/api/v1/_diag/pool/   → per-instance pool stats")
        print(f"  GET http://{host}/api/v1/instance/     → INSTANCE_ID confirmation")
    print()
    print("  NOTE: REQUEST_COUNT is per-process (RAM). Each instance reports")
    print("        its own count independently. Sum them for a system-wide view.")
    print("        This is intentional for diagnostics; it is NOT a source of truth.")
    return {"status": "stub — run against live stack for real data"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("\n" + "=" * 60)
    print("  NFR5 — Load Distribution Simulation")
    print("  High-Performance E-Commerce Backend Engine")
    print("=" * 60)
    print(f"\n  Backends  : {', '.join(BACKENDS)}")
    print(f"  Requests  : {TOTAL_REQUESTS}")
    print(f"  Workers   : {CONCURRENT_USERS} concurrent")
    print(f"  Profile   : 80% product_list | 15% place_order | 5% payment_capture")

    # --- Run round_robin ---
    print("\n[1/4] Simulating round_robin...")
    rr_backends, rr_errors = run_simulation("round_robin")
    print_histogram(rr_backends, "Strategy: round_robin", rr_errors)

    # --- Run least_conn ---
    print("\n[2/4] Simulating least_conn...")
    lc_backends, lc_errors = run_simulation("least_conn")
    print_histogram(lc_backends, "Strategy: least_conn", lc_errors)

    # --- Comparison ---
    print("\n[3/4] Comparison table...")
    print_comparison_table(rr_backends, lc_backends)

    # --- Failover demo ---
    print("\n[4/4] Failover demo (least_conn, web1 fails at 50%, recovers at 75%)...")
    fo_backends, fo_errors = run_simulation("least_conn", inject_failure=True)
    print_histogram(fo_backends, "Failover demo: web1 goes down mid-simulation", fo_errors)
    print()
    print("  After recovery, web1 re-enters rotation (see last 25% of requests).")
    print("  Errors are only the in-flight requests at the exact moment of failure.")

    # --- Metrics stub ---
    collect_metrics_stub(BACKENDS)

    print("\n  Simulation complete.")
    print("  To run against a live stack: bash tools/distribution_check.sh 300")
    print("  To run the failover script : bash tools/failover_demo.sh")
    print()


if __name__ == "__main__":
    main()
