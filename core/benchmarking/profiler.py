"""
Benchmarking + bottleneck analysis - [NFR10].

The deliverable for NFR10 is a numeric BEFORE / AFTER comparison on at
least one identified bottleneck. Tools:

  - django-silk    : per-request DB query profile (already wired in).
  - timing logs    : core.aop.middleware.PerformanceMiddleware.
  - call counts    : core.aop.decorators.count_calls.
  - locust stats   : tests/stress/locustfile.py exports CSV.

Public surface (filled in by NFR10 owner):

  - capture_baseline(scenario_name)
        Saves the current p50/p95/p99 + DB query counts under a name.

  - capture_after(scenario_name)
        Same metrics, after the optimization. Generates a markdown diff
        report in docs/benchmarks/<scenario>.md.

  - top_n_hot_paths(n=10)
        Returns the top-N endpoints sorted by total time, used to pick
        the bottleneck to attack.
"""


def capture_baseline(scenario_name: str) -> dict:
    """Snapshot current performance metrics, persist them under a name."""
    # TODO [NFR10]
    raise NotImplementedError("NFR10 owner must implement capture_baseline")


def capture_after(scenario_name: str) -> dict:
    """Snapshot after the change and write a markdown diff report."""
    # TODO [NFR10]
    raise NotImplementedError("NFR10 owner must implement capture_after")


def top_n_hot_paths(n: int = 10) -> list[tuple[str, float]]:
    """Return [(label, total_ms), ...] sorted desc."""
    # TODO [NFR10]
    raise NotImplementedError("NFR10 owner must implement top_n_hot_paths")
