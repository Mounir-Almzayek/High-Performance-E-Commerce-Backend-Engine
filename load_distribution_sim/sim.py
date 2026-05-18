from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field
from statistics import mean


REQUEST_COSTS_MS = {
    "product_list": 5,
    "order_place": 90,
    "payment_capture": 180,
}

REQUEST_MIX = [
    ("product_list", 0.80),
    ("order_place", 0.15),
    ("payment_capture", 0.05),
]


@dataclass(order=True)
class Backend:
    active_until: list[int] = field(default_factory=list, compare=True)
    name: str = field(default="", compare=False)
    handled: int = field(default=0, compare=False)
    queue_samples: list[int] = field(default_factory=list, compare=False)

    def advance_to(self, now_ms: int) -> None:
        while self.active_until and self.active_until[0] <= now_ms:
            heapq.heappop(self.active_until)

    def assign(self, now_ms: int, cost_ms: int) -> None:
        self.advance_to(now_ms)
        heapq.heappush(self.active_until, now_ms + cost_ms)
        self.handled += 1
        self.queue_samples.append(len(self.active_until))

    @property
    def active(self) -> int:
        return len(self.active_until)

    @property
    def max_queue(self) -> int:
        return max(self.queue_samples or [0])

    @property
    def avg_queue(self) -> float:
        return mean(self.queue_samples or [0])


def choose_request() -> tuple[str, int]:
    roll = random.random()
    cumulative = 0.0
    for name, weight in REQUEST_MIX:
        cumulative += weight
        if roll <= cumulative:
            return name, REQUEST_COSTS_MS[name]
    name = REQUEST_MIX[-1][0]
    return name, REQUEST_COSTS_MS[name]


def run(strategy: str, *, n_requests: int = 1000, seed: int = 42) -> list[Backend]:
    random.seed(seed)
    backends = [Backend(name=f"web{i}") for i in range(1, 4)]
    rr_index = 0

    for request_index in range(n_requests):
        now_ms = request_index * random.randint(1, 4)
        _, cost_ms = choose_request()
        for backend in backends:
            backend.advance_to(now_ms)

        if strategy == "round_robin":
            backend = backends[rr_index % len(backends)]
            rr_index += 1
        elif strategy == "least_conn":
            backend = min(backends, key=lambda item: (item.active, item.name))
        else:
            raise ValueError(f"unknown strategy: {strategy}")

        backend.assign(now_ms, cost_ms)

    return backends


def print_summary(strategy: str, backends: list[Backend]) -> None:
    total = sum(backend.handled for backend in backends)
    print(f"\nStrategy: {strategy}")
    print("Backend  Requests  Percent  Avg queue  Max queue")
    for backend in backends:
        percent = (backend.handled / total) * 100
        print(
            f"{backend.name:<8} {backend.handled:>8} "
            f"{percent:>7.1f}% {backend.avg_queue:>10.2f} {backend.max_queue:>10}"
        )


def main() -> None:
    for strategy in ("round_robin", "least_conn"):
        print_summary(strategy, run(strategy))


if __name__ == "__main__":
    main()
